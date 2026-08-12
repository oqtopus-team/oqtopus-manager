"""Unit tests for shared file lock / edit / save helpers."""

from __future__ import annotations

import datetime
import itertools
import json
import pathlib
import time
import uuid
from types import SimpleNamespace
from typing import Self

import pytest
from fastapi.responses import JSONResponse

from oqtopus_manager.routers import _file_edit
from oqtopus_manager.routers._file_edit import (
    _acquire_file_lock,
    _check_lock,
    _force_unlock_file,
    _release_file_lock,
    _save_file,
)


def _body(resp: JSONResponse) -> dict:
    """Parse a JSONResponse body regardless of bytes/memoryview return type."""
    return json.loads(bytes(resp.body))


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


class TestCheckLock:
    def test_no_lock_file(self, tmp_path: pathlib.Path) -> None:
        is_locked, token, since, since_ts = _check_lock(tmp_path / "test.lock", 600)
        assert is_locked is False
        assert token is None
        assert since is None
        assert since_ts is None

    def test_valid_lock(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        tok = _write_lock(lock_path)
        is_locked, token, since, since_ts = _check_lock(lock_path, 600)
        assert is_locked is True
        assert token == tok
        assert since is not None
        assert since_ts is not None

    def test_expired_lock_removed(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path, ts=0.0)  # epoch 0 is always stale
        is_locked, token, _, _ = _check_lock(lock_path, 600)
        assert is_locked is False
        assert token is None
        assert not lock_path.exists()

    def test_corrupted_lock_removed(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock_path.write_text("token\nnot-a-number", encoding="utf-8")
        is_locked, _, _, _ = _check_lock(lock_path, 600)
        assert is_locked is False
        assert not lock_path.exists()


class TestForceUnlockFile:
    def test_removes_existing_lock(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path)
        resp = _force_unlock_file(lock_path)
        assert resp.status_code == 200
        assert _body(resp)["ok"] is True
        assert not lock_path.exists()

    def test_succeeds_when_no_file(self, tmp_path: pathlib.Path) -> None:
        resp = _force_unlock_file(tmp_path / "absent.lock")
        assert resp.status_code == 200
        assert _body(resp)["ok"] is True


class TestAcquireFileLock:
    def test_success_creates_lock_and_returns_token(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        resp = _acquire_file_lock(lock_path, 600)
        data = _body(resp)
        assert resp.status_code == 200
        assert data["ok"] is True
        assert "token" in data
        assert "acquired_ts" in data
        assert lock_path.exists()

    def test_already_locked_returns_409(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path)
        resp = _acquire_file_lock(lock_path, 600)
        assert resp.status_code == 409
        assert _body(resp)["ok"] is False


class TestReleaseFileLock:
    def test_correct_token_removes_lock(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        tok = _write_lock(lock_path)
        resp = _release_file_lock(lock_path, tok, 600)
        assert resp.status_code == 200
        assert _body(resp)["ok"] is True
        assert not lock_path.exists()

    def test_wrong_token_returns_403(self, tmp_path: pathlib.Path) -> None:
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path)
        resp = _release_file_lock(lock_path, "wrong-token", 600)
        assert resp.status_code == 403
        assert lock_path.exists()


class TestSaveFile:
    def test_success_writes_content_and_backup(self, tmp_path: pathlib.Path) -> None:
        file_path = tmp_path / "test.env"
        lock_path = tmp_path / "test.env.lock"
        file_path.write_text("OLD=value", encoding="utf-8")
        tok = _write_lock(lock_path)
        resp = _save_file(file_path, lock_path, "NEW=value", tok, 600)
        assert _body(resp)["ok"] is True
        assert file_path.read_text(encoding="utf-8") == "NEW=value"
        assert not lock_path.exists()
        backups = list(tmp_path.glob("test.env.*"))
        assert len(backups) == 1

    def test_no_backup_when_original_missing(self, tmp_path: pathlib.Path) -> None:
        file_path = tmp_path / "new.env"
        lock_path = tmp_path / "new.env.lock"
        tok = _write_lock(lock_path)
        resp = _save_file(file_path, lock_path, "KEY=val", tok, 600)
        assert _body(resp)["ok"] is True
        assert file_path.read_text(encoding="utf-8") == "KEY=val"
        assert len(list(tmp_path.glob("new.env.*"))) == 0

    def test_no_lock_returns_409(self, tmp_path: pathlib.Path) -> None:
        file_path = tmp_path / "test.env"
        lock_path = tmp_path / "test.env.lock"
        resp = _save_file(file_path, lock_path, "content", "any-token", 600)
        assert resp.status_code == 409

    def test_wrong_token_returns_403(self, tmp_path: pathlib.Path) -> None:
        file_path = tmp_path / "test.env"
        lock_path = tmp_path / "test.env.lock"
        _write_lock(lock_path)
        resp = _save_file(file_path, lock_path, "content", "wrong-token", 600)
        assert resp.status_code == 403

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
        # _check_lock's staleness check still uses real fromtimestamp() (inherited,
        # unmocked); only now() -- used for the backup filename -- is faked.
        counter = itertools.count()

        class _FakeDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz: datetime.tzinfo | None = None) -> Self:
                base = cls(2024, 1, 1, tzinfo=datetime.UTC)
                return base + datetime.timedelta(seconds=next(counter))

        monkeypatch.setattr(
            _file_edit,
            "datetime",
            SimpleNamespace(datetime=_FakeDateTime, UTC=datetime.UTC),
        )

        for i in range(n_saves):
            tok = _write_lock(lock_path)
            _save_file(file_path, lock_path, f"ITER={i}", tok, 600)
        backups = list(tmp_path.glob("test.env.*"))
        assert len(backups) == n_saves
