"""Wrapper for invoking the oqtopus CLI as a subprocess."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib
    from collections.abc import AsyncGenerator


@dataclass(frozen=True)
class CommandResult:
    """Outcome of a non-streamed ``oqtopus`` subcommand invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether the command exited with status 0.

        Returns:
            True if the command succeeded.

        """
        return self.returncode == 0


async def _drain_stdout_queue(
    queue: asyncio.Queue[bytes | None],
    process: asyncio.subprocess.Process,
    reader_task: asyncio.Task[None],
) -> AsyncGenerator[str]:
    """Yield SSE data lines from *queue* until EOF or the process exits.

    Yields:
        SSE-formatted data lines.

    """
    while True:
        try:
            raw = await asyncio.wait_for(queue.get(), timeout=0.1)
        except TimeoutError:
            if process.returncode is not None:
                reader_task.cancel()
                break
            continue
        if raw is None:
            break
        yield f"data: {raw.decode(errors='replace').rstrip()}\n\n"


async def _cancel_and_await(reader_task: asyncio.Task[None]) -> None:
    """Cancel *reader_task* (if still running) and await its completion.

    Ensures the task is never left for asyncio's weak-reference bookkeeping
    to discover pending at an arbitrary later point (e.g. after an early
    client disconnect closes the generator that spawned it).
    """
    if not reader_task.done():
        reader_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reader_task


async def _stream_command(
    argv: list[str],
    cwd: pathlib.Path,
) -> AsyncGenerator[str]:
    """Run *argv* in *cwd* and yield SSE-formatted strings.

    Yields:
        SSE-formatted strings for streaming to the client.

    Raises:
        RuntimeError: If the subprocess stdout pipe is unexpectedly None.

    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        yield "data: oqtopus command not found. Please install oqtopus-cli first.\n\n"
        yield "event: done\ndata: error\n\n"
        return

    if process.stdout is None:
        msg = "subprocess stdout is None"
        raise RuntimeError(msg)

    # Feed stdout into a queue from a background task so we can stop reading
    # when the parent process exits, even if a spawned daemon keeps the pipe open.
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def _reader() -> None:
        try:
            async for raw in process.stdout:  # type: ignore[union-attr]
                await queue.put(raw)
        finally:
            await queue.put(None)

    reader_task = asyncio.create_task(_reader())

    # The finally clause runs on every exit path, including an early client
    # disconnect (StreamingResponse calls aclose(), throwing GeneratorExit
    # into this generator at the yield below). The subprocess itself is
    # deliberately left running on that path: install-type commands leave
    # the target directory in a recoverable-but-incomplete state, and
    # re-running to completion is safer than killing it mid-way on every
    # disconnect.
    try:
        async for chunk in _drain_stdout_queue(queue, process, reader_task):
            yield chunk
    finally:
        await _cancel_and_await(reader_task)

    await process.wait()
    if process.returncode == 0:
        yield "event: done\ndata: success\n\n"
    else:
        yield "event: done\ndata: error\n\n"


async def stream_oqtopus_init(
    name: str, template: str, cwd: pathlib.Path
) -> AsyncGenerator[str]:
    """Run ``oqtopus init <name> --template <template>`` in *cwd*.

    Yields:
        SSE-formatted strings for streaming to the client.

    """
    async for chunk in _stream_command(
        ["oqtopus", "init", name, "--template", template], cwd
    ):
        yield chunk


async def stream_log_tail(
    log_path: pathlib.Path, tail_lines: int
) -> AsyncGenerator[str]:
    """Stream *log_path* via ``tail -f -n tail_lines``, yielding SSE data lines.

    Yields:
        SSE-formatted strings for streaming to the client.

    Raises:
        RuntimeError: If the subprocess stdout pipe is unexpectedly None.

    """
    try:
        process = await asyncio.create_subprocess_exec(
            "tail",
            "-f",
            "-n",
            str(tail_lines),
            str(log_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        yield "data: 'tail' command not found.\n\n"
        return

    if process.stdout is None:
        msg = "subprocess stdout is None"
        raise RuntimeError(msg)
    try:
        async for raw in process.stdout:
            yield f"data: {raw.decode(errors='replace').rstrip()}\n\n"
    finally:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


async def stream_oqtopus_subcommand(
    subcommand: str, args: list[str], cwd: pathlib.Path
) -> AsyncGenerator[str]:
    """Run ``oqtopus <subcommand> <args>`` in *cwd*.

    Yields:
        SSE-formatted strings for streaming to the client.

    """
    async for chunk in _stream_command(["oqtopus", subcommand, *args], cwd):
        yield chunk


async def run_oqtopus_subcommand_output(
    subcommand: str, args: list[str], cwd: pathlib.Path
) -> CommandResult:
    """Run ``oqtopus <subcommand> <args>`` in *cwd* and capture stdout/stderr.

    Returns:
        CommandResult with the exit code and decoded stdout/stderr, kept
        separate so callers can distinguish a real failure from output that
        merely looks empty.

    Raises:
        RuntimeError: If the subprocess returncode is unexpectedly None after
            ``communicate()`` returns.

    """
    try:
        process = await asyncio.create_subprocess_exec(
            "oqtopus",
            subcommand,
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return CommandResult(
            returncode=127,
            stdout="",
            stderr="oqtopus command not found. Please install oqtopus-cli first.",
        )
    stdout, stderr = await process.communicate()
    if process.returncode is None:
        msg = "subprocess returncode is None after communicate()"
        raise RuntimeError(msg)
    return CommandResult(
        returncode=process.returncode,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )
