"""Route for the application settings page."""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._file_edit import (  # noqa: TC001 (pydantic body models FastAPI needs at runtime)
    _SaveBody,
    _UnlockBody,
)
from oqtopus_manager.routers._utils import _lock_error_response
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import (
    LockConflictError,
    LockExpiredError,
    LockNotHeldError,
    LockTokenMismatchError,
)

if TYPE_CHECKING:
    import pathlib

    from oqtopus_manager.config import AppConfig

router = APIRouter(prefix="/settings", tags=["settings"])
api_router = APIRouter(prefix="/api/settings", tags=["settings-api"])


async def _run_quick(argv: list[str]) -> str:
    """Run a short-lived command and return its output, or an error string.

    Returns:
        The command stdout/stderr output, or an error string on failure.

    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        out = stdout.decode(errors="replace").strip()
        # Prefer stdout; fall back to stderr for commands that only write errors
        return out or stderr.decode(errors="replace").strip()
    except FileNotFoundError:
        return "command not found"
    except TimeoutError:
        return "timeout"


def _which_to_path(which: str, cfg: AppConfig) -> pathlib.Path:
    """Resolve 'config' or 'logging' to the corresponding settings file path.

    Returns:
        The resolved Path for the requested settings file.

    Raises:
        HTTPException: If ``which`` is not a recognised settings file name.

    """
    if which == "config":
        return cfg.config_path
    if which == "logging":
        return cfg.config_path.parent / "logging.yaml"
    raise HTTPException(status_code=400, detail=f"Unknown settings file: {which!r}")


def _build_lock_context(cfg: AppConfig) -> dict:
    """Build lock-state context for config.yaml and logging.yaml.

    Returns:
        Dict with is_locked, locked_since, locked_since_ts for each file.

    """
    config_lock = cfg.config_path.parent / "config.yaml.lock"
    logging_lock = cfg.config_path.parent / "logging.yaml.lock"
    config_state = env_service.check_lock(config_lock, cfg.file_edit_lock_timeout_sec)
    logging_state = env_service.check_lock(logging_lock, cfg.file_edit_lock_timeout_sec)
    return {
        "config_is_locked": config_state.is_locked,
        "config_locked_since": config_state.locked_since,
        "config_locked_since_ts": config_state.locked_since_ts,
        "logging_is_locked": logging_state.is_locked,
        "logging_locked_since": logging_state.locked_since,
        "logging_locked_since_ts": logging_state.locked_since_ts,
        "lock_timeout_sec": cfg.file_edit_lock_timeout_sec,
    }


@router.get(
    "",
    response_class=HTMLResponse,
    dependencies=[require_permission("app_settings.get")],
)
async def settings_page(request: Request) -> HTMLResponse:
    """Render the application settings page.

    Returns:
        HTMLResponse with the rendered settings page.

    """
    cfg = request.app.state.config
    user = request.state.user

    def _read(path: pathlib.Path) -> str:
        return (
            path.read_text(encoding="utf-8")
            if path.exists()
            else f"# File not found: {path}"
        )

    logging_path = cfg.config_path.parent / "logging.yaml"
    environments_path = cfg.environments_file
    # which() returns None if the command is not in PATH
    oqtopus_path = shutil.which("oqtopus") or "not found"
    raw_version = await _run_quick(["oqtopus", "version"])
    oqtopus_version = raw_version.removeprefix("oqtopus ").strip()
    lock_ctx = _build_lock_context(cfg)

    return request.app.state.templates.TemplateResponse(
        request,
        "app_settings.html",
        {
            "config_path": cfg.config_path,
            "config_content": _read(cfg.config_path),
            "logging_path": logging_path,
            "logging_content": _read(logging_path),
            "environments_path": environments_path,
            "environments_content": _read(environments_path),
            "oqtopus_path": oqtopus_path,
            "oqtopus_version": oqtopus_version,
            "can_update": request.app.state.permissions.has_permission(
                user, "app_settings.update"
            ),
            **lock_ctx,
        },
    )


@api_router.post(
    "/{which}/force-unlock",
    dependencies=[require_permission("app_settings.update")],
)
async def force_unlock_settings(request: Request, which: str) -> JSONResponse:
    """Force-unlock a settings file.

    Returns:
        JSONResponse with ok=True on success.

    """
    cfg = request.app.state.config
    file_path = _which_to_path(which, cfg)
    env_service.force_unlock_file(file_path.parent / f"{file_path.name}.lock")
    return JSONResponse({"ok": True})


@api_router.post(
    "/{which}/lock",
    dependencies=[require_permission("app_settings.update")],
)
async def acquire_settings_lock(request: Request, which: str) -> JSONResponse:
    """Acquire a lock on a settings file.

    Returns:
        JSONResponse with ok=True and token on success, or conflict info.

    """
    cfg = request.app.state.config
    file_path = _which_to_path(which, cfg)
    lock_path = file_path.parent / f"{file_path.name}.lock"
    try:
        lock = env_service.acquire_file_lock(lock_path, cfg.file_edit_lock_timeout_sec)
    except LockConflictError as exc:
        return _lock_error_response(exc)
    return JSONResponse({
        "ok": True,
        "token": lock.token,
        "acquired_ts": lock.acquired_ts,
    })


@api_router.post(
    "/{which}/unlock",
    dependencies=[require_permission("app_settings.update")],
)
async def release_settings_lock(
    request: Request, which: str, body: _UnlockBody
) -> JSONResponse:
    """Release a lock on a settings file.

    Returns:
        JSONResponse with ok=True if the lock was released.

    """
    cfg = request.app.state.config
    file_path = _which_to_path(which, cfg)
    lock_path = file_path.parent / f"{file_path.name}.lock"
    try:
        env_service.release_file_lock(
            lock_path, body.token, cfg.file_edit_lock_timeout_sec
        )
    except LockNotHeldError as exc:
        return _lock_error_response(exc)
    return JSONResponse({"ok": True})


@api_router.post(
    "/{which}/save",
    dependencies=[require_permission("app_settings.update")],
)
async def save_settings(request: Request, which: str, body: _SaveBody) -> JSONResponse:
    """Save a settings file after validating the lock token.

    Returns:
        JSONResponse with ok=True on success.

    """
    cfg = request.app.state.config
    file_path = _which_to_path(which, cfg)
    lock_path = file_path.parent / f"{file_path.name}.lock"
    try:
        env_service.save_file(
            file_path,
            lock_path,
            body.content,
            body.token,
            cfg.file_edit_lock_timeout_sec,
        )
    except (LockExpiredError, LockTokenMismatchError) as exc:
        return _lock_error_response(exc)
    return JSONResponse({"ok": True})
