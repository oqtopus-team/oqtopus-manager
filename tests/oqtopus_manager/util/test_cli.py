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
    result = await run_oqtopus_subcommand_output("backend", ["status"], tmp_path)
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
        "backend", ["versions", "engine"], tmp_path
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
    result = await run_oqtopus_subcommand_output("backend", ["status"], tmp_path)
    assert result.ok is False
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "remote status check failed\n"
