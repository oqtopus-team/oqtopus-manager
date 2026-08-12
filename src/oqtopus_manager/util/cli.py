"""Wrapper for invoking the oqtopus CLI as a subprocess."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib
    from collections.abc import AsyncGenerator


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
) -> str:
    """Run ``oqtopus <subcommand> <args>`` in *cwd* and return stdout as a string.

    Returns:
        The command output as a decoded string, or empty string on failure.

    """
    try:
        process = await asyncio.create_subprocess_exec(
            "oqtopus",
            subcommand,
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return ""
    stdout, _ = await process.communicate()
    return stdout.decode(errors="replace")
