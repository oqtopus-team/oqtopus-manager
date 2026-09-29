"""Cloud-local detail page, plus its /api/cloud-local JSON/Server-Sent Events routes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._problem import problem_response
from oqtopus_manager.routers._utils import (
    _get_config,
    _get_held_by,
    _get_lock_registry,
    _get_templates,
)
from oqtopus_manager.services import cloud_local as cloud_local_service
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError
from oqtopus_manager.util.cli import stream_oqtopus_subcommand

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter(prefix="/cloud-local", tags=["cloud-local"])
api_router = APIRouter(prefix="/api/cloud-local", tags=["cloud-local-api"])
logger = logging.getLogger(__name__)

_SUBCOMMAND = "cloud-local"


@dataclass
class _StreamParams:
    cmd: str
    service: str = "all"
    component: str = "cloud"
    version: str = ""
    foreground: bool = False


# ── HTML pages ───────────────────────────────────────────────────────────────


@router.get(
    "/{name}/settings-partial",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.get")],
)
async def get_settings_partial(request: Request, name: str) -> HTMLResponse:
    """Return the settings partial HTML for the given cloud-local environment.

    Returns:
        HTMLResponse with the settings partial template.

    Raises:
        HTTPException: If the environment is not found.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        info = await cloud_local_service.get_info(cfg, name)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    return _get_templates(request).TemplateResponse(
        request,
        "environments/_settings_dl.html",
        {"info": info, "resolved_root_path": resolved},
    )


@router.get(
    "/{name}",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.get")],
)
async def get_environment(request: Request, name: str) -> HTMLResponse:
    """Render the cloud-local environment detail page.

    Returns:
        HTMLResponse with the environment detail page.

    Raises:
        HTTPException: If the environment is not found.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        info = await cloud_local_service.get_info(cfg, name)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    ctx: dict = {
        "env": env,
        "resolved_root_path": resolved,
        "info": info,
        "all_versions_installed": info.all_installed,
        "components": cloud_local_service.COMPONENTS,
    }
    return _get_templates(request).TemplateResponse(
        request, "environments/cloud_local_detail.html", ctx
    )


# ── /api/cloud-local JSON + Server-Sent Events ──────────────────────────────


@api_router.get(
    "/{name}/stream",
    dependencies=[require_permission("environment.service.manage")],
)
async def cloud_local_stream(
    request: Request,
    name: str,
    params: Annotated[_StreamParams, Depends()],
) -> StreamingResponse:
    """Run an oqtopus cloud-local subcommand and stream output as Server-Sent Events.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output from the
        cloud-local command.

    Raises:
        HTTPException: If the environment is not found or command arguments are invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = cloud_local_service.build_stream_args(
            params.cmd,
            params.service,
            params.component,
            params.version,
            params.foreground,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Cloud-local stream: cmd=%s args=%s env=%s", params.cmd, args, name)

    lock = cloud_local_service.stream_lock(
        _get_lock_registry(request),
        params.cmd,
        name,
        params.service,
        params.component,
        operation=operation,
        held_by=_get_held_by(request),
    )

    async def event_stream() -> AsyncGenerator[str]:
        async for chunk in stream_oqtopus_subcommand(
            _SUBCOMMAND,
            args,
            cwd,
            timeout=cfg.oqtopus_cli_operation_timeout_sec,
            lock=lock,
        ):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@api_router.get(
    "/{name}/status",
    dependencies=[require_permission("environment.get")],
)
async def get_status(request: Request, name: str) -> JSONResponse:
    """Run ``oqtopus cloud-local status`` and return it as JSON.

    Returns:
        JSONResponse with StatusData, or an RFC 9457 problem response.

    """
    cfg = _get_config(request)
    try:
        data = await cloud_local_service.get_status(cfg, name)
    except ServiceError as exc:
        return problem_response(exc)
    return JSONResponse(data.model_dump())


@api_router.get(
    "/{name}",
    dependencies=[require_permission("environment.get")],
)
async def get_environment_info(request: Request, name: str) -> JSONResponse:
    """Run ``oqtopus cloud-local info`` and return it as JSON (the env resource).

    Returns:
        JSONResponse with EnvironmentData, or an RFC 9457 problem response.

    """
    cfg = _get_config(request)
    try:
        data = await cloud_local_service.get_info(cfg, name)
    except ServiceError as exc:
        return problem_response(exc)
    return JSONResponse(data.model_dump())


@api_router.get(
    "/{name}/components/{component}/versions",
    dependencies=[require_permission("environment.get")],
)
async def get_component_versions(
    request: Request, name: str, component: str
) -> JSONResponse:
    """Run ``oqtopus cloud-local versions <component>`` and return it as JSON.

    Replaces the old ``GET /cloud-local/{name}/component-versions?component=``:
    path and response shape both change, so this is a breaking change
    rather than a plain /api move.

    Returns:
        JSONResponse with VersionsData, or an RFC 9457 problem response.

    """
    cfg = _get_config(request)
    try:
        data = await cloud_local_service.get_component_versions_detailed(
            cfg, name, component
        )
    except ServiceError as exc:
        return problem_response(exc)
    return JSONResponse(data.model_dump())


@api_router.get(
    "/{name}/locks",
    dependencies=[require_permission("environment.service.manage")],
)
async def get_locks(request: Request, name: str) -> JSONResponse:  # ruff: ignore[unused-function-argument]
    """Return every currently-held exclusive operation lock.

    Nested under an environment for URL symmetry with the other routes,
    but returns the full process-wide snapshot regardless of *name*: a
    component lock blocking this environment's install may have been
    acquired by a completely different one, and an operator diagnosing a
    stuck operation needs to see that too.

    Returns:
        JSONResponse with a list of {scope, operation, held_by, held_since}.

    """
    registry = _get_lock_registry(request)
    return JSONResponse([
        {
            "scope": info.scope,
            "operation": info.operation,
            "held_by": info.held_by,
            "held_since": info.held_since,
        }
        for info in registry.snapshot()
    ])


@api_router.post(
    "/{name}/locks/force-unlock",
    dependencies=[require_permission("environment.service.manage")],
)
async def force_unlock_lock(
    request: Request,
    name: str,  # ruff: ignore[unused-function-argument]
    scope: str,
) -> JSONResponse:
    """Forcibly clear one lock reported by ``GET .../locks``.

    Does not stop whatever CLI subprocess it belongs to -- see
    ``LockRegistry.force_unlock`` for the safety trade-off this accepts.

    Returns:
        JSONResponse ``{"ok": true}`` if *scope* was held (and is now
        cleared), ``{"ok": false}`` if nothing was held there.

    """
    freed = await _get_lock_registry(request).force_unlock(scope)
    return JSONResponse({"ok": freed})
