"""Routes for cloud-local detail, settings-partial, stream, and component-versions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._utils import _get_config, _get_templates
from oqtopus_manager.services import cloud_local as cloud_local_service
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError
from oqtopus_manager.util.cli import stream_oqtopus_subcommand

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter(prefix="/cloud-local", tags=["cloud-local"])
logger = logging.getLogger(__name__)

_SUBCOMMAND = "cloud-local"


@dataclass
class _StreamParams:
    cmd: str
    service: str = "all"
    component: str = "cloud"
    version: str = ""
    foreground: bool = False


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
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    meta = cloud_local_service.read_metadata(resolved)
    return _get_templates(request).TemplateResponse(
        request,
        "environments/_settings_dl.html",
        {
            "meta": meta,
            "resolved_root_path": resolved,
            "version_keys": cloud_local_service.VERSION_KEYS,
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
    """Run oqtopus cloud-local versions <component> and return parsed version list.

    Returns:
        JSONResponse with a list of version strings.

    Raises:
        HTTPException: If the component is invalid or environment is not found.

    """
    cfg = _get_config(request)
    try:
        versions = await cloud_local_service.get_component_versions(
            cfg, name, component
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse({"versions": versions})


@router.get(
    "/{name}/stream",
    dependencies=[require_permission("environment.service.manage")],
)
async def cloud_local_stream(
    request: Request,
    name: str,
    params: Annotated[_StreamParams, Depends()],
) -> StreamingResponse:
    """SSE endpoint: run an oqtopus cloud-local subcommand and stream its output.

    Returns:
        StreamingResponse with SSE-formatted output from the cloud-local command.

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
    logger.info("Cloud-local stream: cmd=%s args=%s env=%s", params.cmd, args, name)

    async def event_stream() -> AsyncGenerator[str]:
        async for chunk in stream_oqtopus_subcommand(_SUBCOMMAND, args, cwd):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    meta = cloud_local_service.read_metadata(resolved)
    ctx: dict = {
        "env": env,
        "resolved_root_path": resolved,
        "meta": meta,
        "all_versions_installed": bool(
            meta.get("cloud_version")
            and meta.get("frontend_version")
            and meta.get("admin_version")
        ),
        "version_keys": cloud_local_service.VERSION_KEYS,
    }
    return _get_templates(request).TemplateResponse(
        request, "environments/cloud_local_detail.html", ctx
    )
