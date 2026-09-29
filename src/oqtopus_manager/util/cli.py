"""Wrapper for invoking the oqtopus CLI as a subprocess."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
    """Outcome of a non-streamed ``oqtopus`` subcommand invocation.

    ``returncode`` is None to represent a timeout: the process was killed
    before it could exit, so there is no exit status to report.
    """

    returncode: int | None
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether the command exited with status 0.

        Returns:
            True if the command succeeded.

        """
        return self.returncode == 0

    @property
    def timed_out(self) -> bool:
        """Whether the command was killed for exceeding its timeout.

        Returns:
            True if no exit status was ever observed.

        """
        return self.returncode is None


async def _drain_stdout_queue(
    queue: asyncio.Queue[bytes | None],
    process: asyncio.subprocess.Process,
    reader_task: asyncio.Task[None],
) -> AsyncGenerator[str]:
    """Yield Server-Sent Events data lines from *queue* until EOF or the process exits.

    Yields:
        Server-Sent Events-formatted data lines.

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


async def _kill_on_timeout(
    process: asyncio.subprocess.Process,
    timeout: float,  # ruff: ignore[async-function-with-timeout]
) -> bool:
    """Wait for *process* to exit, killing it if *timeout* seconds elapse first.

    Returns:
        True if the process had to be killed (it exceeded *timeout*).

    """
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        return True
    return False


async def _supervise_process(
    process: asyncio.subprocess.Process,
    argv: list[str],
    timeout: float | None,  # ruff: ignore[async-function-with-timeout]
    release_lock: Callable[[], Awaitable[None]],
) -> bool:
    """Own *process*'s lifetime (and release its lock) once it exits.

    Runs as a detached background task, independent of whether the SSE
    consumer in ``_stream_command`` is still listening, so a client
    disconnect can never leave a hung process holding the lock forever.

    Returns:
        True if the process had to be killed for exceeding *timeout*. A
        killed process still gets a real (non-None) OS returncode, so this
        can't be inferred from ``process.returncode`` afterward -- it has
        to be tracked here, at the point where the kill actually happens.

    """
    timed_out = False
    try:
        if timeout is not None:
            timed_out = await _kill_on_timeout(process, timeout)
        else:
            await process.wait()
    except Exception:
        logger.exception("Error supervising subprocess: %s", argv)
    finally:
        await release_lock()
    return timed_out


def _start_reader_and_supervisor(
    process: asyncio.subprocess.Process,
    argv: list[str],
    timeout: float | None,
    lock: AbstractAsyncContextManager[None] | None,
    release_lock: Callable[[], Awaitable[None]],
) -> tuple[asyncio.Queue[bytes | None], asyncio.Task[None], asyncio.Task[bool] | None]:
    """Spawn the stdout-reader task and, if needed, the lifetime-supervisor task.

    The supervisor task is only spawned when there's a timeout or a lock to
    manage, so callers that pass neither see no behavior change at all.

    Returns:
        The stdout queue, the reader task, and the supervisor task (or None).

    """
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def _reader() -> None:
        try:
            async for raw in process.stdout:  # type: ignore[union-attr]
                await queue.put(raw)
        finally:
            await queue.put(None)

    reader_task = asyncio.create_task(_reader())
    supervisor_task = None
    if timeout is not None or lock is not None:
        supervisor_task = asyncio.create_task(
            _supervise_process(process, argv, timeout, release_lock)
        )
    return queue, reader_task, supervisor_task


def _done_event(returncode: int | None, *, timed_out: bool) -> str:
    """Return the SSE ``event: done`` line matching *returncode*/*timed_out*.

    Returns:
        The formatted SSE line: "timeout", "success", or "error".

    """
    if timed_out:
        return "event: done\ndata: timeout\n\n"
    if returncode == 0:
        return "event: done\ndata: success\n\n"
    return "event: done\ndata: error\n\n"


async def _stream_command(
    argv: list[str],
    cwd: pathlib.Path,
    *,
    timeout: float | None = None,  # ruff: ignore[async-function-with-timeout]
    lock: AbstractAsyncContextManager[None] | None = None,
) -> AsyncGenerator[str]:
    """Run *argv* in *cwd* and yield Server-Sent Events-formatted strings.

    If *lock* is given, it is entered before the subprocess is spawned and
    only released once the subprocess actually exits -- normally, or killed
    after *timeout* seconds -- regardless of whether this generator itself
    is torn down early by a client disconnect (see the note below on why
    the subprocess is left running on disconnect). Holding the lock's
    release hostage to "the SSE consumer is still listening" would defeat
    its purpose: an orphaned-but-still-running install must keep the lock.

    Yields:
        Server-Sent Events-formatted strings for streaming to the client.

    Raises:
        RuntimeError: If the subprocess stdout pipe is unexpectedly None.

    """
    lock_released = False

    async def _release_lock() -> None:
        nonlocal lock_released
        if lock is not None and not lock_released:
            lock_released = True
            await lock.__aexit__(None, None, None)

    if lock is not None:
        await lock.__aenter__()  # ruff: ignore[unnecessary-dunder-call]

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        await _release_lock()
        yield "data: oqtopus command not found. Please install oqtopus-cli first.\n\n"
        yield "event: done\ndata: error\n\n"
        return

    if process.stdout is None:
        await _release_lock()
        msg = "subprocess stdout is None"
        raise RuntimeError(msg)

    # Feed stdout into a queue from a background task so we can stop reading
    # when the parent process exits, even if a spawned daemon keeps the pipe open.
    # The supervisor task (if any) is deliberately not awaited or referenced
    # in the `finally` below: it must keep running -- and eventually release
    # the lock -- even if the client disconnects and this generator is torn
    # down early.
    queue, reader_task, supervisor_task = _start_reader_and_supervisor(
        process, argv, timeout, lock, _release_lock
    )

    # The finally clause runs on every exit path, including an early client
    # disconnect (StreamingResponse calls aclose(), throwing GeneratorExit
    # into this generator at the yield below). The subprocess itself is
    # deliberately left running on that path: install-type commands leave
    # the target directory in a recoverable-but-incomplete state, and
    # re-running to completion is safer than killing it mid-way on every
    # disconnect. (A hang is different -- that's what *timeout* is for.)
    try:
        async for chunk in _drain_stdout_queue(queue, process, reader_task):
            yield chunk
    finally:
        await _cancel_and_await(reader_task)

    if supervisor_task is not None:
        timed_out = await supervisor_task
    else:
        await process.wait()
        timed_out = False
    yield _done_event(process.returncode, timed_out=timed_out)


async def stream_oqtopus_init(
    name: str,
    template: str,
    cwd: pathlib.Path,
    *,
    timeout: float | None = None,  # ruff: ignore[async-function-with-timeout]
    lock: AbstractAsyncContextManager[None] | None = None,
) -> AsyncGenerator[str]:
    """Run ``oqtopus init <name> --template <template>`` in *cwd*.

    Yields:
        Server-Sent Events-formatted strings for streaming to the client.

    """
    async for chunk in _stream_command(
        ["oqtopus", "init", name, "--template", template],
        cwd,
        timeout=timeout,
        lock=lock,
    ):
        yield chunk


async def stream_log_tail(
    log_path: pathlib.Path, tail_lines: int
) -> AsyncGenerator[str]:
    """Stream *log_path* via ``tail -f -n tail_lines`` as Server-Sent Events.

    Yields:
        Server-Sent Events-formatted strings for streaming to the client.

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
    subcommand: str,
    args: list[str],
    cwd: pathlib.Path,
    *,
    timeout: float | None = None,  # ruff: ignore[async-function-with-timeout]
    lock: AbstractAsyncContextManager[None] | None = None,
) -> AsyncGenerator[str]:
    """Run ``oqtopus <subcommand> <args>`` in *cwd*.

    Yields:
        Server-Sent Events-formatted strings for streaming to the client.

    """
    async for chunk in _stream_command(
        ["oqtopus", subcommand, *args], cwd, timeout=timeout, lock=lock
    ):
        yield chunk


async def run_oqtopus_subcommand_output(
    subcommand: str,
    args: list[str],
    cwd: pathlib.Path,
    timeout: float,  # ruff: ignore[async-function-with-timeout]
) -> CommandResult:
    """Run ``oqtopus <subcommand> <args>`` in *cwd* and capture stdout/stderr.

    If the process has not exited within *timeout* seconds, it is killed and
    a CommandResult with ``returncode=None`` (see ``timed_out``) is returned.

    Returns:
        CommandResult with the exit code and decoded stdout/stderr, kept
        separate so callers can distinguish a real failure from output that
        merely looks empty.

    Raises:
        RuntimeError: If the subprocess returncode is unexpectedly None after
            ``communicate()`` returns (outside of the timeout path).

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
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        return CommandResult(
            returncode=None,
            stdout="",
            stderr=(
                f"oqtopus {subcommand} {' '.join(args)} timed out after {timeout}s"
            ),
        )
    if process.returncode is None:
        msg = "subprocess returncode is None after communicate()"
        raise RuntimeError(msg)
    return CommandResult(
        returncode=process.returncode,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )
