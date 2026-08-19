"""Unit tests for services/environment.py."""

from __future__ import annotations

import datetime
import itertools
import pathlib
import time
import uuid
from types import SimpleNamespace
from typing import Self

import pytest
from pytest_mock import MockerFixture

from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import (
    LockConflictError,
    LockExpiredError,
    LockNotHeldError,
    LockTokenMismatchError,
)
from oqtopus_manager.util.cli import CommandResult


def _write_lock(
    lock_path: pathlib.Path,
    token: str | None = None,
    ts: float | None = None,
) -> str:
    """Write a lock file and return the token used."""
    tok = token or str(uuid.uuid4())
    stamp = ts if ts is not None else time.time()
    lock_path.write_text(f"{tok}\n{stamp}", encoding="utf-8")
    return tok


# ── has_running_services ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_has_running_services_true_when_any_service_running(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0,
            stdout="db: Stopped\nworker: Running (PID 123)\n",
            stderr="",
        ),
    )
    assert await env_service.has_running_services("cloud-local", tmp_path) is True


@pytest.mark.anyio
async def test_has_running_services_false_when_all_stopped(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0, stdout="db: Stopped\nworker: Stopped\n", stderr=""
        ),
    )
    assert await env_service.has_running_services("cloud-local", tmp_path) is False


@pytest.mark.anyio
async def test_has_running_services_false_on_empty_output(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    """No components installed yet: a successful status check with nothing to report."""
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=0, stdout="", stderr=""),
    )
    assert await env_service.has_running_services("backend", tmp_path) is False


@pytest.mark.anyio
async def test_has_running_services_is_case_insensitive(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0, stdout="core: RUNNING (PID 1)\n", stderr=""
        ),
    )
    assert await env_service.has_running_services("backend", tmp_path) is True


@pytest.mark.anyio
async def test_has_running_services_fails_closed_on_command_failure(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    """A failed status check (e.g. remote fleet unreachable) must report
    "running" rather than "no services running", so callers don't mistake
    an unknown state for a safe one.
    """
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=1, stdout="", stderr="connection to remote fleet failed"
        ),
    )
    assert await env_service.has_running_services("backend", tmp_path) is True


@pytest.mark.anyio
async def test_has_running_services_fails_closed_when_cli_not_found(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=127,
            stdout="",
            stderr="oqtopus command not found. Please install oqtopus-cli first.",
        ),
    )
    assert await env_service.has_running_services("backend", tmp_path) is True


# ── check_lock ───────────────────────────────────────────────────────────────


class TestCheckLock:
    def test_no_lock_file(self, tmp_path: pathlib.Path) -> None:
        state = env_service.check_lock(tmp_path / "test.lock", 600)
        assert state.is_locked is False
        assert state.token is None
        assert state.locked_since is None
        assert state.locked_since_ts is None

    def test_valid_lock(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        tok = _write_lock(lock_path)
        state = env_service.check_lock(lock_path, 600)
        assert state.is_locked is True
        assert state.token == tok
        assert state.locked_since is not None
        assert state.locked_since_ts is not None

    def test_expired_lock_removed(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path, ts=0.0)  # epoch 0 is always stale
        state = env_service.check_lock(lock_path, 600)
        assert state.is_locked is False
        assert state.token is None
        assert not lock_path.exists()

    def test_corrupted_lock_removed(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock_path.write_text("token\nnot-a-number", encoding="utf-8")
        state = env_service.check_lock(lock_path, 600)
        assert state.is_locked is False
        assert not lock_path.exists()


# ── force_unlock_file ─────────────────────────────────────────────────────────


class TestForceUnlockFile:
    def test_removes_existing_lock(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path)
        env_service.force_unlock_file(lock_path)
        assert not lock_path.exists()

    def test_succeeds_when_no_file(self, tmp_path: pathlib.Path) -> None:
        env_service.force_unlock_file(tmp_path / "absent.lock")  # must not raise


# ── acquire_file_lock ──────────────────────────────────────────────────────────


class TestAcquireFileLock:
    def test_success_creates_lock_and_returns_token(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        result = env_service.acquire_file_lock(lock_path, 600)
        assert result.token
        assert result.acquired_ts
        assert lock_path.exists()

    def test_already_locked_raises_conflict(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path)
        with pytest.raises(LockConflictError):
            env_service.acquire_file_lock(lock_path, 600)


# ── release_file_lock ──────────────────────────────────────────────────────────


class TestReleaseFileLock:
    def test_correct_token_removes_lock(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        tok = _write_lock(lock_path)
        env_service.release_file_lock(lock_path, tok, 600)
        assert not lock_path.exists()

    def test_wrong_token_raises(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path)
        with pytest.raises(LockNotHeldError):
            env_service.release_file_lock(lock_path, "wrong-token", 600)
        assert lock_path.exists()


# ── save_file ──────────────────────────────────────────────────────────────────


class TestSaveFile:
    def test_success_writes_content_and_backup(self, tmp_path: pathlib.Path) -> None:
        file_path = tmp_path / "test.env"
        lock_path = tmp_path / "test.env.lock"
        file_path.write_text("OLD=value", encoding="utf-8")
        tok = _write_lock(lock_path)
        env_service.save_file(file_path, lock_path, "NEW=value", tok, 600)
        assert file_path.read_text(encoding="utf-8") == "NEW=value"
        assert not lock_path.exists()
        backups = list(tmp_path.glob("test.env.*"))
        assert len(backups) == 1

    def test_no_backup_when_original_missing(self, tmp_path: pathlib.Path) -> None:
        file_path = tmp_path / "new.env"
        lock_path = tmp_path / "new.env.lock"
        tok = _write_lock(lock_path)
        env_service.save_file(file_path, lock_path, "KEY=val", tok, 600)
        assert file_path.read_text(encoding="utf-8") == "KEY=val"
        assert len(list(tmp_path.glob("new.env.*"))) == 0

    def test_no_lock_raises_expired(self, tmp_path: pathlib.Path) -> None:
        file_path = tmp_path / "test.env"
        lock_path = tmp_path / "test.env.lock"
        with pytest.raises(LockExpiredError):
            env_service.save_file(file_path, lock_path, "content", "any-token", 600)

    def test_wrong_token_raises_mismatch(self, tmp_path: pathlib.Path) -> None:
        file_path = tmp_path / "test.env"
        lock_path = tmp_path / "test.env.lock"
        _write_lock(lock_path)
        with pytest.raises(LockTokenMismatchError):
            env_service.save_file(file_path, lock_path, "content", "wrong-token", 600)

    @pytest.mark.parametrize("n_saves", [2, 3])
    def test_multiple_saves_create_distinct_backups(
        self,
        tmp_path: pathlib.Path,
        n_saves: int,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        file_path = tmp_path / "test.env"
        lock_path = tmp_path / "test.env.lock"
        file_path.write_text("ORIGINAL=1", encoding="utf-8")

        # Advance the clock by 1 simulated second per call instead of sleeping,
        # so distinct (second-granularity) backup timestamps are still exercised.
        # check_lock's staleness check still uses real fromtimestamp() (inherited,
        # unmocked); only now() -- used for the backup filename -- is faked.
        counter = itertools.count()

        class _FakeDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz: datetime.tzinfo | None = None) -> Self:
                base = cls(2024, 1, 1, tzinfo=datetime.UTC)
                return base + datetime.timedelta(seconds=next(counter))

        monkeypatch.setattr(
            env_service,
            "datetime",
            SimpleNamespace(datetime=_FakeDateTime, UTC=datetime.UTC),
        )

        for i in range(n_saves):
            tok = _write_lock(lock_path)
            env_service.save_file(file_path, lock_path, f"ITER={i}", tok, 600)
        backups = list(tmp_path.glob("test.env.*"))
        assert len(backups) == n_saves
