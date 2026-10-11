"""Unit tests for util/cli.py async helpers."""

from __future__ import annotations

import asyncio
import pathlib
from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

from oqtopus_manager.util.cli import (
    CommandResult,
    _stream_command,
    run_oqtopus_subcommand_output,
    stream_log_tail,
    stream_oqtopus_init,
    stream_oqtopus_subcommand,
)

_NONEXISTENT = "oqtopus-manager-totally-nonexistent-cmd-xyz"


async def _collect(gen) -> list[str]:
    chunks: list[str] = []
    async for chunk in gen:
        chunks.append(chunk)
    return chunks


# ── _stream_command ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_stream_command_file_not_found(tmp_path: pathlib.Path) -> None:
    chunks = await _collect(_stream_command([_NONEXISTENT], tmp_path))
    assert any("not found" in c or "command not found" in c for c in chunks)
    assert any("event: done" in c and "error" in c for c in chunks)


@pytest.mark.anyio
async def test_stream_command_echo_yields_output_and_done(
    tmp_path: pathlib.Path,
) -> None:
    chunks = await _collect(_stream_command(["echo", "hello-stream-test"], tmp_path))
    assert any("hello-stream-test" in c for c in chunks)
    assert any("event: done" in c and "success" in c for c in chunks)


@pytest.mark.anyio
async def test_stream_command_error_exit_yields_error_done(
    tmp_path: pathlib.Path,
) -> None:
    # `false` always exits with code 1 → covers the else branch (error done event)
    chunks = await _collect(_stream_command(["false"], tmp_path))
    assert any("event: done" in c and "error" in c for c in chunks)


@pytest.mark.anyio
async def test_stream_command_queue_timeout_path(
    tmp_path: pathlib.Path,
) -> None:
    # `sleep 0.2` produces no output for > 0.1s, triggering the TimeoutError branch
    chunks = await _collect(_stream_command(["sleep", "0.2"], tmp_path))
    assert any("event: done" in c and "success" in c for c in chunks)


@pytest.mark.anyio
async def test_stream_command_early_close_cancels_reader_without_killing_process(
    tmp_path: pathlib.Path,
) -> None:
    """Closing the generator early (simulating a client disconnect) must not
    leave the background reader task dangling — it should be cancelled and
    awaited — but the subprocess itself must be left running to completion.
    """
    marker = tmp_path / "marker"
    baseline = asyncio.all_tasks()

    gen = _stream_command(
        ["bash", "-c", f"echo start; sleep 0.3; touch {marker}"], tmp_path
    )
    async for _chunk in gen:
        break  # consume exactly one chunk, then abandon the generator

    reader_tasks = {
        t
        for t in asyncio.all_tasks() - baseline
        if (coro := t.get_coro()) is not None and "_reader" in coro.__qualname__
    }
    assert len(reader_tasks) == 1
    reader_task = next(iter(reader_tasks))
    assert not reader_task.done()

    await gen.aclose()

    # The reader task must be cleaned up synchronously by aclose(), not left
    # for the event loop's weak-reference GC to find at some later point.
    assert reader_task.done()
    assert reader_task.cancelled()
    assert asyncio.all_tasks() - baseline == set()

    # The subprocess was NOT killed: it keeps running and creates the marker.
    await asyncio.sleep(0.5)
    assert marker.exists()


class _FakeLock:
    """Records enter/exit for asserting lock lifetime, independent of pytest-mock."""

    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> None:
        self.entered = True

    async def __aexit__(self, *_exc: object) -> bool:
        self.exited = True
        return False


@pytest.mark.anyio
async def test_stream_command_lock_held_for_full_process_lifetime(
    tmp_path: pathlib.Path,
) -> None:
    lock = _FakeLock()
    chunks = await _collect(
        _stream_command(["echo", "hi"], tmp_path, lock=lock)
    )
    assert any("event: done" in c and "success" in c for c in chunks)
    assert lock.entered is True
    assert lock.exited is True


@pytest.mark.anyio
async def test_stream_command_lock_survives_early_client_disconnect(
    tmp_path: pathlib.Path,
) -> None:
    """A client disconnect must not release the lock early: the subprocess is
    left running in the background (see the test above), so the lock must
    stay held until it actually finishes -- otherwise a second install could
    start while the orphaned first one is still writing.
    """
    lock = _FakeLock()
    marker = tmp_path / "marker"

    gen = _stream_command(
        ["bash", "-c", f"echo start; sleep 0.3; touch {marker}"],
        tmp_path,
        lock=lock,
    )
    async for _chunk in gen:
        break  # consume exactly one chunk, then abandon the generator

    await gen.aclose()
    assert lock.entered is True
    assert lock.exited is False  # not released just because the client left

    await asyncio.sleep(0.5)
    assert marker.exists()
    assert lock.exited is True  # released once the background process finished


@pytest.mark.anyio
async def test_stream_command_timeout_kills_process_and_releases_lock(
    tmp_path: pathlib.Path,
) -> None:
    lock = _FakeLock()
    chunks = await _collect(
        _stream_command(["sleep", "10"], tmp_path, timeout=0.05, lock=lock)
    )
    assert any("event: done" in c and "timeout" in c for c in chunks)
    assert lock.entered is True
    assert lock.exited is True


@pytest.mark.anyio
async def test_stream_command_timeout_without_lock_still_kills_process(
    tmp_path: pathlib.Path,
) -> None:
    chunks = await _collect(_stream_command(["sleep", "10"], tmp_path, timeout=0.05))
    assert any("event: done" in c and "timeout" in c for c in chunks)


def _alive(pid: int) -> bool:
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


async def _wait_gone(pid: int, limit: float = 2.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while _alive(pid):
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


@pytest.mark.anyio
async def test_stream_command_timeout_kills_descendants_too(
    tmp_path: pathlib.Path,
) -> None:
    # The background sleep stands in for e.g. a docker build the CLI started:
    # it holds stdout open, so killing only the CLI would leave it running
    # and keep the lock held until it finished on its own.
    pid_file = tmp_path / "child.pid"
    lock = _FakeLock()
    loop = asyncio.get_running_loop()
    started = loop.time()
    chunks = await _collect(
        _stream_command(
            ["sh", "-c", f"sleep 30 & echo $! > {pid_file}; sleep 30"],
            tmp_path,
            timeout=0.2,
            lock=lock,
        )
    )
    assert loop.time() - started < 5
    assert chunks[-1] == "event: done\ndata: timeout\n\n"
    assert lock.exited is True
    assert await _wait_gone(int(pid_file.read_text(encoding="utf-8")))


@pytest.mark.anyio
async def test_stream_command_timeout_escalates_to_sigkill(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch("oqtopus_manager.util.cli._KILL_GRACE_SEC", 0.2)
    loop = asyncio.get_running_loop()
    started = loop.time()
    # Ignored signals are inherited across exec, so sleep ignores SIGTERM too.
    chunks = await _collect(
        _stream_command(
            ["sh", "-c", "trap '' TERM; sleep 30"], tmp_path, timeout=0.2
        )
    )
    assert loop.time() - started < 5
    assert chunks[-1] == "event: done\ndata: timeout\n\n"


@pytest.mark.anyio
async def test_stream_command_normal_exit_leaves_descendants_running(
    tmp_path: pathlib.Path,
) -> None:
    # A process the CLI deliberately leaves behind (e.g. a started service,
    # detached from stdout) outliving the timeout must not be killed: only
    # the CLI's own exit counts against the timeout.
    pid_file = tmp_path / "child.pid"
    chunks = await _collect(
        _stream_command(
            [
                "sh",
                "-c",
                f"sleep 2 >/dev/null 2>&1 & echo $! > {pid_file}",
            ],
            tmp_path,
            timeout=0.5,
        )
    )
    assert chunks[-1] == "event: done\ndata: success\n\n"
    pid = int(pid_file.read_text(encoding="utf-8"))
    await asyncio.sleep(0.8)
    assert _alive(pid)
    assert await _wait_gone(pid, limit=5)


@pytest.mark.anyio
async def test_stream_command_finishes_when_cli_exits_despite_open_pipe(
    tmp_path: pathlib.Path,
) -> None:
    # The CLI exits, but a process it left behind still holds stdout open.
    # The command counts as finished (and its lock is released) at the CLI's
    # exit, not whenever that leftover process eventually closes the pipe.
    lock = _FakeLock()
    loop = asyncio.get_running_loop()
    started = loop.time()
    chunks = await _collect(
        _stream_command(
            ["sh", "-c", "sleep 3 & echo hi"], tmp_path, timeout=10, lock=lock
        )
    )
    # Not 3 s: the stream ends one EOF grace period after the CLI exits.
    assert loop.time() - started < 2.5
    assert chunks == ["data: hi\n\n", "event: done\ndata: success\n\n"]
    assert lock.exited is True


@pytest.mark.anyio
async def test_stream_command_keeps_all_output_written_right_before_exit(
    tmp_path: pathlib.Path,
) -> None:
    chunks = await _collect(_stream_command(["seq", "1", "20000"], tmp_path))
    assert len(chunks) == 20001
    assert chunks[0] == "data: 1\n\n"
    assert chunks[-2] == "data: 20000\n\n"
    assert chunks[-1] == "event: done\ndata: success\n\n"


@pytest.mark.anyio
async def test_stream_command_reads_output_arriving_shortly_after_exit(
    tmp_path: pathlib.Path,
) -> None:
    # Output that reaches the pipe a moment after the CLI exits (here from a
    # leftover child; under load, from a delayed read) is still delivered.
    chunks = await _collect(
        _stream_command(["sh", "-c", "(sleep 0.3; echo late) & echo early"], tmp_path)
    )
    assert chunks == [
        "data: early\n\n",
        "data: late\n\n",
        "event: done\ndata: success\n\n",
    ]


# ── stream_oqtopus_init ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_stream_oqtopus_init_delegates(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    async def _fake(*_args: object, **_kwargs: object):
        yield "data: init\n\n"
        yield "event: done\ndata: success\n\n"

    mocker.patch("oqtopus_manager.util.cli._stream_command", new=_fake)
    chunks = await _collect(stream_oqtopus_init("demo", "backend", tmp_path))
    assert "data: init\n\n" in chunks


# ── stream_oqtopus_subcommand ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_stream_oqtopus_subcommand_delegates(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    async def _fake(*_args: object, **_kwargs: object):
        yield "data: cmd output\n\n"

    mocker.patch("oqtopus_manager.util.cli._stream_command", new=_fake)
    chunks = await _collect(
        stream_oqtopus_subcommand("backend", ["status"], tmp_path)
    )
    assert "data: cmd output\n\n" in chunks


# ── stream_log_tail ───────────────────────────────────────────────────────────


class _FakeStdout:
    """Async iterator over fixed byte lines."""

    def __init__(self, lines: list[bytes]) -> None:
        self._iter = iter(lines)

    def __aiter__(self) -> _FakeStdout:
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeProcess:
    def __init__(self, lines: list[bytes]) -> None:
        self.returncode = 0
        self.stdout = _FakeStdout(lines)

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        pass


@pytest.mark.anyio
async def test_stream_log_tail_file_not_found(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=FileNotFoundError),
    )
    chunks = await _collect(stream_log_tail(tmp_path / "app.log", 10))
    assert any("not found" in c for c in chunks)


@pytest.mark.anyio
async def test_stream_log_tail_yields_log_lines(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    fake_proc = _FakeProcess([b"log line 1\n", b"log line 2\n"])
    mocker.patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    )
    log_file = tmp_path / "app.log"
    log_file.write_text("log line 1\nlog line 2\n")
    chunks = await _collect(stream_log_tail(log_file, 10))
    assert any("log line 1" in c for c in chunks)
    assert any("log line 2" in c for c in chunks)


# ── run_oqtopus_subcommand_output ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_oqtopus_subcommand_output_not_found(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    # Simulate oqtopus binary not installed → FileNotFoundError → not ok
    mocker.patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=FileNotFoundError),
    )
    result = await run_oqtopus_subcommand_output("backend", ["status"], tmp_path, 10)
    assert result == CommandResult(
        returncode=127,
        stdout="",
        stderr="oqtopus command not found. Please install oqtopus-cli first.",
    )
    assert result.ok is False


@pytest.mark.anyio
async def test_run_oqtopus_subcommand_output_returns_stdout(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"v1.2.3\n", b""

    mocker.patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_Proc()),
    )
    result = await run_oqtopus_subcommand_output(
        "backend", ["versions", "engine"], tmp_path, 10
    )
    assert result == CommandResult(returncode=0, stdout="v1.2.3\n", stderr="")
    assert result.ok is True


@pytest.mark.anyio
async def test_run_oqtopus_subcommand_output_reports_failure(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    """Non-zero exit and stderr must be visible to the caller, not merged
    into stdout and silently discarded.
    """

    class _Proc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"remote status check failed\n"

    mocker.patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_Proc()),
    )
    result = await run_oqtopus_subcommand_output("backend", ["status"], tmp_path, 10)
    assert result.ok is False
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "remote status check failed\n"


@pytest.mark.anyio
async def test_run_oqtopus_subcommand_output_timeout(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    class _SlowProc:
        returncode = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> None:
            return None

    mocker.patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_SlowProc()),
    )
    result = await run_oqtopus_subcommand_output(
        "backend", ["status"], tmp_path, 0.05
    )
    assert result.timed_out is True
    assert result.returncode is None
    assert result.ok is False
    assert "timed out" in result.stderr
