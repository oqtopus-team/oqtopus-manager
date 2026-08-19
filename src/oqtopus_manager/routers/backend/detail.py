"""Routes for backend detail, settings-partial, stream, and component-versions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._utils import _get_config, _get_templates
from oqtopus_manager.services import backend as backend_service
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError
from oqtopus_manager.util.cli import stream_oqtopus_subcommand

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter(prefix="/backend", tags=["backend"])
logger = logging.getLogger(__name__)


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
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    meta = env_service.read_metadata(resolved)
    return _get_templates(request).TemplateResponse(
        request,
        "environments/_settings_dl.html",
        {
            "meta": meta,
            "resolved_root_path": resolved,
            "version_keys": ["engine_version", "tranqu_version", "gateway_version"],
        },
    )


@router.get(
    "/{name}/component-versions",
    dependencies=[require_permission("environment.get")],
)
async def component_versions_list(
    request: Request,
    name: str,
    component: str,
) -> JSONResponse:
    """Run oqtopus backend versions <component> and return parsed version list.

    Returns:
        JSONResponse with a list of version strings.

    Raises:
        HTTPException: If the component is invalid or environment is not found.

    """
    cfg = _get_config(request)
    try:
        versions = await backend_service.get_component_versions(cfg, name, component)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse({"versions": versions})


@router.get(
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
    """SSE endpoint: run an oqtopus backend subcommand and stream its output.

    Returns:
        StreamingResponse with SSE-formatted output from the backend command.

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
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    meta = env_service.read_metadata(resolved)
    ctx: dict = {
        "env": env,
        "resolved_root_path": resolved,
        "meta": meta,
        "all_versions_installed": bool(
            meta.get("engine_version")
            and meta.get("tranqu_version")
            and meta.get("gateway_version")
        ),
        "version_keys": ["engine_version", "tranqu_version", "gateway_version"],
    }
    return _get_templates(request).TemplateResponse(
        request, "environments/backend_detail.html", ctx
    )
