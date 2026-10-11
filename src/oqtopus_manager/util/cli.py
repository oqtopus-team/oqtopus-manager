"""Wrapper for invoking the oqtopus CLI as a subprocess."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

logger = logging.getLogger(__name__)

# Seconds between SIGTERM and SIGKILL when a timed-out command is stopped.
_KILL_GRACE_SEC = 5.0
_EXIT_POLL_SEC = 0.1
_QUEUE_POLL_SEC = 0.1
# How long to keep reading output after the process has exited, waiting for EOF.
_EOF_GRACE_SEC = 1.0


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


@dataclass
class CommandExit:
    """How a command run by ``run_command`` ended.

    Filled in only once its output has been read to the end; still empty if
    the reader stopped early.
    """

    returncode: int | None = None
    timed_out: bool = False


async def _drain_stdout_queue(
    queue: asyncio.Queue[bytes | None],
    process: asyncio.subprocess.Process,
    reader_task: asyncio.Task[None],
) -> AsyncGenerator[str]:
    """Yield decoded output lines from *queue* until EOF, or shortly after exit.

    The pipe normally reaches EOF right after the process exits. Reading
    continues for up to ``_EOF_GRACE_SEC`` after the exit is noticed, so
    output still in the pipe (or delayed by a busy event loop) isn't
    dropped; a leftover process holding the pipe open only delays the end
    of the stream by that much.

    Yields:
        One output line at a time, without its trailing newline.

    """
    loop = asyncio.get_running_loop()
    deadline: float | None = None
    while True:
        if deadline is None and process.returncode is not None:
            deadline = loop.time() + _EOF_GRACE_SEC
        if deadline is not None and loop.time() >= deadline:
            reader_task.cancel()
            break
        try:
            raw = await asyncio.wait_for(queue.get(), timeout=_QUEUE_POLL_SEC)
        except TimeoutError:
            continue
        if raw is None:
            break
        yield raw.decode(errors="replace").rstrip()


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


async def _wait_exited(process: asyncio.subprocess.Process) -> None:
    """Wait until *process* itself has exited.

    This is what "the command finished" means here. ``process.wait()``
    would also wait for every pipe to close, which a descendant still
    holding stdout (e.g. a service the command started) delays
    indefinitely. If the command exits while leaving something behind that
    still writes to a shared resource, that is a bug in the command.
    asyncio exposes no exit-only event to await, hence the polling.
    """
    while process.returncode is None:  # ruff: ignore[async-busy-wait]
        await asyncio.sleep(_EXIT_POLL_SEC)


async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    """Stop *process* and every descendant still in its process group.

    SIGTERM first, so the CLI (and e.g. a ``docker build`` it started) can
    cancel cleanly; SIGKILL for whatever is left after the grace period.
    *process* must have been started with ``start_new_session=True``, so
    its process group is its own and never includes this server.
    """
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(_wait_exited(process), timeout=_KILL_GRACE_SEC)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


async def _kill_on_timeout(
    process: asyncio.subprocess.Process,
    timeout: float,  # ruff: ignore[async-function-with-timeout]
) -> bool:
    """Wait for *process* to exit, killing its process group after *timeout* seconds.

    Only the CLI process's own exit counts: a descendant it deliberately
    left running (e.g. a started service) is never killed, even if it still
    holds the output pipe open.

    Returns:
        True if the process had to be killed (it exceeded *timeout*).

    """
    try:
        await asyncio.wait_for(_wait_exited(process), timeout=timeout)
    except TimeoutError:
        await _kill_process_group(process)
        await _wait_exited(process)
        return True
    return False


async def _supervise_process(
    process: asyncio.subprocess.Process,
    argv: list[str],
    timeout: float | None,  # ruff: ignore[async-function-with-timeout]
    release_lock: Callable[[], Awaitable[None]] | None,
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
            await _wait_exited(process)
    except Exception:
        logger.exception("Error supervising subprocess: %s", argv)
    finally:
        if release_lock is not None:
            await release_lock()
    return timed_out


def _start_reader_and_supervisor(
    process: asyncio.subprocess.Process,
    argv: list[str],
    timeout: float | None,
    release_lock: Callable[[], Awaitable[None]] | None,
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
    if timeout is not None or release_lock is not None:
        supervisor_task = asyncio.create_task(
            _supervise_process(process, argv, timeout, release_lock)
        )
    return queue, reader_task, supervisor_task


def command_outcome(returncode: int | None, *, timed_out: bool) -> str:
    """Classify how a command ended.

    Returns:
        "timeout", "success", or "error".

    """
    if timed_out:
        return "timeout"
    if returncode == 0:
        return "success"
    return "error"


def done_event(returncode: int | None, *, timed_out: bool) -> str:
    """Return the SSE ``event: done`` line matching *returncode*/*timed_out*.

    Returns:
        The formatted SSE line: "timeout", "success", or "error".

    """
    return f"event: done\ndata: {command_outcome(returncode, timed_out=timed_out)}\n\n"


async def run_command(
    argv: list[str],
    cwd: pathlib.Path,
    *,
    result: CommandExit,
    timeout: float | None = None,  # ruff: ignore[async-function-with-timeout]
    release_lock: Callable[[], Awaitable[None]] | None = None,
) -> AsyncGenerator[str]:
    """Run *argv* in *cwd* and yield its combined stdout/stderr line by line.

    This is the part shared by every streamed CLI call; callers decide where
    the lines go (straight to one HTTP response, or into an operation's ring
    buffer). *result* is filled in once the output has been read to the end.

    *release_lock*, if given, is called exactly once, when the subprocess
    actually exits -- normally, or killed together with every process it
    started (its process group) after *timeout* seconds -- from a
    detached supervisor task, so it still happens if this generator is
    closed early (see the note below on why the subprocess is left running
    on that path).

    Yields:
        One output line at a time, without its trailing newline.

    Raises:
        RuntimeError: If the subprocess stdout pipe is unexpectedly None.

    """
    try:
        # A session (and process group) of its own, so a timeout can stop
        # everything the command started -- not just the command itself.
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError:
        if release_lock is not None:
            await release_lock()
        yield "oqtopus command not found. Please install oqtopus-cli first."
        result.returncode = 127
        return

    if process.stdout is None:
        if release_lock is not None:
            await release_lock()
        msg = "subprocess stdout is None"
        raise RuntimeError(msg)

    # Feed stdout into a queue from a background task so we can stop reading
    # when the parent process exits, even if a spawned daemon keeps the pipe open.
    # The supervisor task (if any) is deliberately not awaited or referenced
    # in the `finally` below: it must keep running -- and eventually release
    # the lock -- even if the reader goes away and this generator is closed
    # early.
    queue, reader_task, supervisor_task = _start_reader_and_supervisor(
        process, argv, timeout, release_lock
    )

    # The finally clause runs on every exit path, including an early close
    # (e.g. a client disconnect: StreamingResponse calls aclose(), throwing
    # GeneratorExit into this generator at the yield below). The subprocess
    # itself is deliberately left running on that path: install-type
    # commands leave the target directory in a recoverable-but-incomplete
    # state, and re-running to completion is safer than killing it mid-way
    # on every disconnect. (A hang is different -- that's what *timeout* is for.)
    try:
        async for line in _drain_stdout_queue(queue, process, reader_task):
            yield line
    finally:
        await _cancel_and_await(reader_task)

    if supervisor_task is not None:
        result.timed_out = await supervisor_task
    else:
        await _wait_exited(process)
    result.returncode = process.returncode


async def _stream_command(
    argv: list[str],
    cwd: pathlib.Path,
    *,
    timeout: float | None = None,  # ruff: ignore[async-function-with-timeout]
    lock: AbstractAsyncContextManager[None] | None = None,
) -> AsyncGenerator[str]:
    """Run *argv* in *cwd* and yield Server-Sent Events-formatted strings.

    If *lock* is given, it is entered (waiting if necessary) before the
    subprocess is spawned and only released once the subprocess actually
    exits, regardless of whether this generator itself is torn down early
    by a client disconnect. Holding the lock's release hostage to "the SSE
    consumer is still listening" would defeat its purpose: an
    orphaned-but-still-running install must keep the lock.

    Yields:
        Server-Sent Events-formatted strings for streaming to the client.

    """
    lock_released = False

    async def _release_lock() -> None:
        nonlocal lock_released
        if lock is not None and not lock_released:
            lock_released = True
            await lock.__aexit__(None, None, None)

    if lock is not None:
        await lock.__aenter__()  # ruff: ignore[unnecessary-dunder-call]

    result = CommandExit()
    lines = run_command(
        argv,
        cwd,
        result=result,
        timeout=timeout,
        release_lock=_release_lock if lock is not None else None,
    )
    # Closed explicitly so a client disconnect reaches run_command's own
    # cleanup right away, rather than whenever the generator is collected.
    try:
        async for line in lines:
            yield f"data: {line}\n\n"
    finally:
        await lines.aclose()
    yield done_event(result.returncode, timed_out=result.timed_out)


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
