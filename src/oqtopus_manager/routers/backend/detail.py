"""Backend detail HTML page, plus its /api/backend JSON/Server-Sent Events routes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._problem import problem_response
from oqtopus_manager.routers._utils import _get_config, _get_templates
from oqtopus_manager.services import backend as backend_service
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError
from oqtopus_manager.util.cli import stream_oqtopus_subcommand

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter(prefix="/backend", tags=["backend"])
api_router = APIRouter(prefix="/api/backend", tags=["backend-api"])
logger = logging.getLogger(__name__)


# ── HTML pages ───────────────────────────────────────────────────────────────


@router.get(
    "/{name}/settings-partial",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.get")],
)
async def get_settings_partial(request: Request, name: str) -> HTMLResponse:
    """Return the settings partial HTML for the given environment.

    Returns:
        HTMLResponse with the settings partial template.

    Raises:
        HTTPException: If the environment is not found.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        info = await backend_service.get_info(cfg, name)
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
    """Render the environment detail page.

    Returns:
        HTMLResponse with the environment detail page.

    Raises:
        HTTPException: If the environment is not found.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        info = await backend_service.get_info(cfg, name)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    ctx: dict = {
        "env": env,
        "resolved_root_path": resolved,
        "info": info,
        "all_versions_installed": info.all_installed,
        "components": backend_service.COMPONENTS,
    }
    return _get_templates(request).TemplateResponse(
        request, "environments/backend_detail.html", ctx
    )


# ── /api/backend JSON + Server-Sent Events ──────────────────────────────────


@api_router.get(
    "/{name}/stream",
    dependencies=[require_permission("environment.service.manage")],
)
async def backend_stream(  # noqa: PLR0913, PLR0917
    request: Request,
    name: str,
    cmd: str,
    service: str = "all",
    component: str = "engine",
    version: str = "",
    foreground: bool = False,  # noqa: FBT001, FBT002
    status: str = "",
    skip_sse_build: bool = False,  # noqa: FBT001, FBT002
) -> StreamingResponse:
    """Run an oqtopus backend subcommand and stream its output as Server-Sent Events.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output from the
        backend command.

    Raises:
        HTTPException: If the environment is not found or command arguments are invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        backend_args = backend_service.build_stream_args(
            cmd, service, component, version, foreground, status, skip_sse_build
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    logger.info("Backend stream: cmd=%s args=%s env=%s", cmd, backend_args, name)

    async def event_stream() -> AsyncGenerator[str]:
        async for chunk in stream_oqtopus_subcommand("backend", backend_args, cwd):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@api_router.get(
    "/{name}/status",
    dependencies=[require_permission("environment.get")],
)
async def get_status(request: Request, name: str) -> JSONResponse:
    """Run ``oqtopus backend status`` and return it as JSON.

    Returns:
        JSONResponse with StatusData, or an RFC 9457 problem response.

    """
    cfg = _get_config(request)
    try:
        data = await backend_service.get_status(cfg, name)
    except ServiceError as exc:
        return problem_response(exc)
    return JSONResponse(data.model_dump())


@api_router.get(
    "/{name}/device-status",
    dependencies=[require_permission("environment.get")],
)
async def get_device_status(request: Request, name: str) -> JSONResponse:
    """Run ``oqtopus backend device-status show`` and return it as JSON.

    Returns:
        JSONResponse with DeviceStatusData, or an RFC 9457 problem response.

    """
    cfg = _get_config(request)
    try:
        data = await backend_service.get_device_status(cfg, name)
    except ServiceError as exc:
        return problem_response(exc)
    return JSONResponse(data.model_dump())


@api_router.get(
    "/{name}",
    dependencies=[require_permission("environment.get")],
)
async def get_environment_info(request: Request, name: str) -> JSONResponse:
    """Run ``oqtopus backend info`` and return it as JSON (the environment resource).

    Returns:
        JSONResponse with EnvironmentData, or an RFC 9457 problem response.

    """
    cfg = _get_config(request)
    try:
        data = await backend_service.get_info(cfg, name)
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
    """Run ``oqtopus backend versions <component>`` and return it as JSON.

    Replaces the old ``GET /backend/{name}/component-versions?component=``:
    path and response shape both change, so this is a breaking change
    rather than a plain /api move.

    Returns:
        JSONResponse with VersionsData, or an RFC 9457 problem response.

    """
    cfg = _get_config(request)
    try:
        data = await backend_service.get_component_versions_detailed(
            cfg, name, component
        )
    except ServiceError as exc:
        return problem_response(exc)
    return JSONResponse(data.model_dump())
