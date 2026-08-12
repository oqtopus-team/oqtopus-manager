"""Shared helpers for file lock/edit/save operations."""

from __future__ import annotations

import datetime
import logging
import shutil
import time
import uuid
from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    import pathlib

logger = logging.getLogger(__name__)


def _parse_lock_file(
    lock_path: pathlib.Path, timeout: int
) -> tuple[bool, str | None, str | None, float | None] | None:
    """Parse a lock file and return lock info if the lock is still active.

    Returns:
        Lock info tuple if still active, or None if stale.

    """
    parts = lock_path.read_text(encoding="utf-8").strip().split("\n", 1)
    token = parts[0]
    # Default to epoch 0 so a missing timestamp is always treated as stale
    ts = float(parts[1]) if len(parts) > 1 else 0.0
    if time.time() - ts >= timeout:
        return None
    locked_since = datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return True, token, locked_since, ts


def _check_lock(
    lock_path: pathlib.Path, timeout: int
) -> tuple[bool, str | None, str | None, float | None]:
    """Return (is_locked, token_if_locked, locked_since_str, locked_since_ts).

    Removes stale or corrupted lock files automatically.

    Returns:
        Tuple of (is_locked, token, locked_since, locked_since_ts).

    """
    if not lock_path.exists():
        return False, None, None, None
    try:
        result = _parse_lock_file(lock_path, timeout)
    except ValueError, OSError:
        # Treat corrupted lock files the same as stale ones
        result = None
    if result is None:
        lock_path.unlink(missing_ok=True)
        return False, None, None, None
    return result


def _force_unlock_file(lock_path: pathlib.Path) -> JSONResponse:
    """Remove a lock file unconditionally.

    Returns:
        JSONResponse with ok=True.

    """
    lock_path.unlink(missing_ok=True)
    logger.warning("Force-unlocked: %s", lock_path)
    return JSONResponse({"ok": True})


def _acquire_file_lock(lock_path: pathlib.Path, timeout: int) -> JSONResponse:
    """Acquire a lock on the given lock file path.

    Returns:
        JSONResponse with ok=True and token on success, or 409 if already locked.

    """
    is_locked, _, locked_since, locked_since_ts = _check_lock(lock_path, timeout)
    if is_locked:
        return JSONResponse(
            {
                "ok": False,
                "locked_since": locked_since,
                "locked_since_ts": locked_since_ts,
            },
            status_code=409,
        )
    ts = time.time()
    token = str(uuid.uuid4())  # 128-bit random; uuid4 avoids MAC-address/time leakage
    lock_path.write_text(f"{token}\n{ts}", encoding="utf-8")
    return JSONResponse({"ok": True, "token": token, "acquired_ts": ts})


def _release_file_lock(
    lock_path: pathlib.Path, token: str, timeout: int
) -> JSONResponse:
    """Release a lock if the token matches.

    Returns:
        JSONResponse with ok=True if released, 403 if token mismatch.

    """
    is_locked, stored_token, _, __ = _check_lock(lock_path, timeout)
    if is_locked and stored_token == token:
        lock_path.unlink(missing_ok=True)
        return JSONResponse({"ok": True})
    return JSONResponse(
        {"ok": False, "error": "Lock not held or token mismatch."}, status_code=403
    )


def _save_file(
    file_path: pathlib.Path,
    lock_path: pathlib.Path,
    content: str,
    token: str,
    timeout: int,
) -> JSONResponse:
    """Validate lock token, back up the file, write new content, and release lock.

    Returns:
        JSONResponse with ok=True on success, error JSON on failure.

    """
    is_locked, stored_token, _, __ = _check_lock(lock_path, timeout)
    if not is_locked:
        return JSONResponse({"ok": False, "error": "Lock expired."}, status_code=409)
    if stored_token != token:
        return JSONResponse({"ok": False, "error": "Invalid token."}, status_code=403)
    if file_path.exists():
        # Timestamped backup so every save is recoverable without external VCS
        backup_ts = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d%H%M%S")
        backup_path = file_path.parent / f"{file_path.name}.{backup_ts}"
        shutil.copy2(file_path, backup_path)
        logger.debug("Backup created: %s", backup_path)
    file_path.write_text(content, encoding="utf-8")
    lock_path.unlink(missing_ok=True)
    logger.info("Saved: %s", file_path)
    return JSONResponse({"ok": True})


class _UnlockBody(BaseModel):
    token: str


class _SaveBody(BaseModel):
    token: str
    content: str
