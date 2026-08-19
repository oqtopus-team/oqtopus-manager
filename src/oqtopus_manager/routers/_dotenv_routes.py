"""Shared dotenv route factory used by all environment template types."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._file_edit import (  # noqa: TC001 (pydantic body models FastAPI needs at runtime)
    _SaveBody,
    _UnlockBody,
)
from oqtopus_manager.routers._utils import (
    _get_config,
    _get_templates,
    _lock_error_response,
)
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import (
    LockConflictError,
    LockExpiredError,
    LockNotHeldError,
    LockTokenMismatchError,
    ServiceError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def make_dotenv_router(  # noqa: C901, PLR0915
    url_prefix: str,
    tags: Sequence[str],
    *,
    release_diff_raw_url: str,
    release_diff_display_url: str,
) -> APIRouter:
    """Return an APIRouter with all .env editor routes wired to ``url_prefix``.

    Args:
        url_prefix: URL prefix for the router (e.g. "/backend").
        tags: OpenAPI tags for the router.
        release_diff_raw_url: Raw GitHub URL of the upstream .env template.
        release_diff_display_url: Browser-friendly GitHub URL shown in the diff panel.

    Returns:
        APIRouter with force-unlock, lock, unlock, save, download, release-diff,
        and view routes.

    """
    router = APIRouter(prefix=url_prefix, tags=tags)  # type: ignore[arg-type]

    @router.post(
        "/{name}/dotenv/force-unlock",
        dependencies=[require_permission("environment.config.update")],
    )
    async def force_unlock_dotenv(request: Request, name: str) -> JSONResponse:
        cfg = _get_config(request)
        try:
            env = env_service.get_environment_or_404(name, cfg)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        env_service.force_unlock_file(resolved / "config" / ".env.lock")
        return JSONResponse({"ok": True})

    @router.post(
        "/{name}/dotenv/lock",
        dependencies=[require_permission("environment.config.update")],
    )
    async def acquire_dotenv_lock(request: Request, name: str) -> JSONResponse:
        cfg = _get_config(request)
        try:
            env = env_service.get_environment_or_404(name, cfg)
            resolved = env.resolved_root_path(cfg.default_environment_base_path)
            lock = env_service.acquire_file_lock(
                resolved / "config" / ".env.lock", cfg.file_edit_lock_timeout_sec
            )
        except LockConflictError as exc:
            return _lock_error_response(exc)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return JSONResponse({
            "ok": True,
            "token": lock.token,
            "acquired_ts": lock.acquired_ts,
        })

    @router.post(
        "/{name}/dotenv/unlock",
        dependencies=[require_permission("environment.config.update")],
    )
    async def release_dotenv_lock(
        request: Request, name: str, body: _UnlockBody
    ) -> JSONResponse:
        cfg = _get_config(request)
        try:
            env = env_service.get_environment_or_404(name, cfg)
            resolved = env.resolved_root_path(cfg.default_environment_base_path)
            env_service.release_file_lock(
                resolved / "config" / ".env.lock",
                body.token,
                cfg.file_edit_lock_timeout_sec,
            )
        except LockNotHeldError as exc:
            return _lock_error_response(exc)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return JSONResponse({"ok": True})

    @router.post(
        "/{name}/dotenv/save",
        dependencies=[require_permission("environment.config.update")],
    )
    async def save_dotenv(request: Request, name: str, body: _SaveBody) -> JSONResponse:
        cfg = _get_config(request)
        try:
            env = env_service.get_environment_or_404(name, cfg)
            resolved = env.resolved_root_path(cfg.default_environment_base_path)
            dotenv_path = resolved / "config" / ".env"
            lock_path = resolved / "config" / ".env.lock"
            env_service.save_file(
                dotenv_path,
                lock_path,
                body.content,
                body.token,
                cfg.file_edit_lock_timeout_sec,
            )
        except (LockExpiredError, LockTokenMismatchError) as exc:
            return _lock_error_response(exc)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return JSONResponse({"ok": True})

    @router.get(
        "/{name}/dotenv/download",
        dependencies=[require_permission("environment.config.get")],
    )
    async def environment_dotenv_download(request: Request, name: str) -> FileResponse:
        cfg = _get_config(request)
        try:
            env = env_service.get_environment_or_404(name, cfg)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        dotenv_path = (
            env.resolved_root_path(cfg.default_environment_base_path)
            / "config"
            / ".env"
        )
        if not dotenv_path.exists():
            raise HTTPException(status_code=404, detail="config/.env not found.")
        return FileResponse(path=dotenv_path, filename=".env", media_type="text/plain")

    @router.get(
        "/{name}/dotenv/release-diff",
        dependencies=[require_permission("environment.config.get")],
    )
    async def dotenv_release_diff(name: str) -> JSONResponse:  # noqa: ARG001
        content = await env_service.fetch_dotenv_template(release_diff_raw_url)
        return JSONResponse({
            "installed_content": content,
            "installed_path": release_diff_display_url,
        })

    @router.get(
        "/{name}/dotenv",
        response_class=HTMLResponse,
        dependencies=[require_permission("environment.config.get")],
    )
    async def environment_dotenv(request: Request, name: str) -> HTMLResponse:
        cfg = _get_config(request)
        try:
            env = env_service.get_environment_or_404(name, cfg)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        dotenv_path = resolved / "config" / ".env"
        lock_path = resolved / "config" / ".env.lock"
        lock_state = env_service.check_lock(lock_path, cfg.file_edit_lock_timeout_sec)

        return _get_templates(request).TemplateResponse(
            request,
            "environments/dotenv.html",
            {
                "env": env,
                "url_prefix": url_prefix,
                "dotenv_path": dotenv_path,
                "dotenv_content": (
                    dotenv_path.read_text(encoding="utf-8")
                    if dotenv_path.exists()
                    else None
                ),
                "is_locked": lock_state.is_locked,
                "locked_since": lock_state.locked_since,
                "locked_since_ts": lock_state.locked_since_ts,
                "lock_timeout_sec": cfg.file_edit_lock_timeout_sec,
            },
        )

    return router
