"""Unit tests for services/environment.py."""

from __future__ import annotations

import datetime
import itertools
import pathlib
import time
import uuid
from types import SimpleNamespace
from typing import Self
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from pytest_mock import MockerFixture

from oqtopus_manager.models.environment import Environment
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import (
    CliNotFoundError,
    CliTimeoutError,
    CommandFailedError,
    EnvironmentAlreadyExistsError,
    LockConflictError,
    LockExpiredError,
    LockNotHeldError,
    LockTokenMismatchError,
    ReservedEnvironmentNameError,
    ServiceError,
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
    assert await env_service.has_running_services("cloud-local", tmp_path, 10) is True


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
    assert await env_service.has_running_services("cloud-local", tmp_path, 10) is False


@pytest.mark.anyio
async def test_has_running_services_false_on_empty_output(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    """No components installed yet: a successful status check with nothing to report."""
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=0, stdout="", stderr=""),
    )
    assert await env_service.has_running_services("backend", tmp_path, 10) is False


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
    assert await env_service.has_running_services("backend", tmp_path, 10) is True


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
    assert await env_service.has_running_services("backend", tmp_path, 10) is True


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
    assert await env_service.has_running_services("backend", tmp_path, 10) is True


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


# ── raise_for_command_result ─────────────────────────────────────────────────


class TestRaiseForCommandResult:
    def test_ok_does_not_raise(self) -> None:
        env_service.raise_for_command_result(
            CommandResult(returncode=0, stdout="", stderr="")
        )

    def test_timeout_raises_cli_timeout_error(self) -> None:
        with pytest.raises(CliTimeoutError):
            env_service.raise_for_command_result(
                CommandResult(returncode=None, stdout="", stderr="timed out")
            )

    def test_127_raises_cli_not_found_error(self) -> None:
        with pytest.raises(CliNotFoundError):
            env_service.raise_for_command_result(
                CommandResult(returncode=127, stdout="", stderr="not found")
            )

    def test_other_nonzero_raises_command_failed_error(self) -> None:
        with pytest.raises(CommandFailedError) as exc_info:
            env_service.raise_for_command_result(
                CommandResult(returncode=1, stdout="", stderr="boom")
            )
        assert exc_info.value.returncode == 1
        assert "boom" in str(exc_info.value)


# ── reserved environment names ───────────────────────────────────────────────


class TestReservedEnvironmentNames:
    def test_validate_new_environment_rejects_reserved_name(
        self, tmp_path: pathlib.Path
    ) -> None:
        cfg = MagicMock(load_environments=lambda: [])
        with pytest.raises(ReservedEnvironmentNameError):
            env_service.validate_new_environment(cfg, "new", "backend", "")

    def test_validate_new_environment_rejects_stream(self) -> None:
        cfg = MagicMock(load_environments=lambda: [])
        with pytest.raises(ReservedEnvironmentNameError):
            env_service.validate_new_environment(cfg, "stream", "backend", "")

    def test_validate_new_environment_allows_non_reserved_name(self) -> None:
        cfg = MagicMock(load_environments=lambda: [])
        env_service.validate_new_environment(cfg, "my-env", "backend", "")  # no raise

    def test_existing_name_check_still_wins_only_when_not_reserved(self) -> None:
        cfg = MagicMock(
            load_environments=lambda: [Environment(name="dup", template="backend")]
        )
        with pytest.raises(EnvironmentAlreadyExistsError):
            env_service.validate_new_environment(cfg, "dup", "backend", "")

    def test_check_reserved_environment_names_detects_collision(self) -> None:
        cfg = MagicMock(
            load_environments=lambda: [
                Environment(name="new", template="backend"),
                Environment(name="ok-env", template="backend"),
            ]
        )
        assert env_service.check_reserved_environment_names(cfg) == ["new"]

    def test_check_reserved_environment_names_empty_when_none_collide(self) -> None:
        cfg = MagicMock(
            load_environments=lambda: [Environment(name="ok-env", template="backend")]
        )
        assert env_service.check_reserved_environment_names(cfg) == []


# ── build_environment_list ───────────────────────────────────────────────────


class _FakeData(BaseModel):
    """Stand-in for the real backend/cloud_local pydantic models in these tests."""

    name: str | None = None
    all_installed: bool | None = None
    state: str | None = None
    device_status: str | None = None


class TestBuildEnvironmentList:
    @pytest.mark.anyio
    async def test_reports_per_environment_data(self) -> None:
        cfg = MagicMock()
        envs = [Environment(name="a", template="backend")]

        async def get_info(cfg: object, name: str) -> _FakeData:
            return _FakeData(name=name, all_installed=True)

        async def get_status(cfg: object, name: str) -> _FakeData:
            return _FakeData(state="running")

        result = await env_service.build_environment_list(
            cfg, envs, "backend",
            include_status=True, has_device_status=False,
            get_info=get_info, get_status=get_status, get_device_status=None,
        )
        assert result["template"] == "backend"
        entry = result["environments"][0]
        assert entry["name"] == "a"
        assert entry["environment"].data.name == "a"
        assert entry["status"].data.state == "running"

    @pytest.mark.anyio
    async def test_status_not_fetched_when_not_all_installed(self) -> None:
        cfg = MagicMock()
        envs = [Environment(name="a", template="backend")]
        status_calls = []

        async def get_info(cfg: object, name: str) -> _FakeData:
            return _FakeData(all_installed=False)

        async def get_status(cfg: object, name: str) -> _FakeData:
            status_calls.append(name)
            return _FakeData()

        result = await env_service.build_environment_list(
            cfg, envs, "backend",
            include_status=True, has_device_status=False,
            get_info=get_info, get_status=get_status, get_device_status=None,
        )
        assert status_calls == []
        assert result["environments"][0]["status"] is None

    @pytest.mark.anyio
    async def test_status_not_fetched_when_include_status_false(self) -> None:
        cfg = MagicMock()
        envs = [Environment(name="a", template="backend")]

        async def get_info(cfg: object, name: str) -> _FakeData:
            return _FakeData(all_installed=True)

        async def get_status(cfg: object, name: str) -> _FakeData:
            msg = "must not be called"
            raise AssertionError(msg)

        result = await env_service.build_environment_list(
            cfg, envs, "backend",
            include_status=False, has_device_status=False,
            get_info=get_info, get_status=get_status, get_device_status=None,
        )
        assert result["environments"][0]["status"] is None

    @pytest.mark.anyio
    async def test_info_failure_is_captured_as_error_outcome(self) -> None:
        cfg = MagicMock()
        envs = [Environment(name="a", template="backend")]

        async def get_info(cfg: object, name: str) -> _FakeData:
            raise CommandFailedError("boom", returncode=1)

        async def get_status(cfg: object, name: str) -> _FakeData:
            msg = "must not be called"
            raise AssertionError(msg)

        result = await env_service.build_environment_list(
            cfg, envs, "backend",
            include_status=True, has_device_status=False,
            get_info=get_info, get_status=get_status, get_device_status=None,
        )
        entry = result["environments"][0]
        assert entry["environment"].data is None
        assert isinstance(entry["environment"].error, ServiceError)
        assert entry["status"] is None

    @pytest.mark.anyio
    async def test_one_environment_failing_does_not_affect_others(self) -> None:
        cfg = MagicMock()
        envs = [
            Environment(name="broken", template="backend"),
            Environment(name="ok", template="backend"),
        ]

        async def get_info(cfg: object, name: str) -> _FakeData:
            if name == "broken":
                raise CommandFailedError("boom", returncode=1)
            return _FakeData(all_installed=False)

        result = await env_service.build_environment_list(
            cfg, envs, "backend",
            include_status=False, has_device_status=False,
            get_info=get_info, get_status=get_info, get_device_status=None,
        )
        by_name = {e["name"]: e for e in result["environments"]}
        assert by_name["broken"]["environment"].error is not None
        assert by_name["ok"]["environment"].data is not None

    @pytest.mark.anyio
    async def test_device_status_fetched_for_backend_when_all_installed(self) -> None:
        cfg = MagicMock()
        envs = [Environment(name="a", template="backend")]

        async def get_info(cfg: object, name: str) -> _FakeData:
            return _FakeData(all_installed=True)

        async def get_status(cfg: object, name: str) -> _FakeData:
            return _FakeData()

        async def get_device_status(cfg: object, name: str) -> _FakeData:
            return _FakeData(device_status="active")

        result = await env_service.build_environment_list(
            cfg, envs, "backend",
            include_status=True, has_device_status=True,
            get_info=get_info, get_status=get_status,
            get_device_status=get_device_status,
        )
        entry = result["environments"][0]
        assert entry["device_status"].data.device_status == "active"

    @pytest.mark.anyio
    async def test_no_device_status_key_when_has_device_status_false(self) -> None:
        cfg = MagicMock()
        envs = [Environment(name="a", template="backend")]

        async def get_info(cfg: object, name: str) -> _FakeData:
            return _FakeData(all_installed=False)

        result = await env_service.build_environment_list(
            cfg, envs, "cloud-local",
            include_status=True, has_device_status=False,
            get_info=get_info, get_status=get_info, get_device_status=None,
        )
        assert "device_status" not in result["environments"][0]
