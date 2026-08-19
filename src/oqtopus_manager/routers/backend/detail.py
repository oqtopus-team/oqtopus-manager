"""Routes for backend detail, settings-partial, stream, and component-versions."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._utils import (
    _get_config,
    _get_environment_or_404,
    _get_templates,
)
from oqtopus_manager.routers.backend._utils import _read_metadata
from oqtopus_manager.util.cli import (
    run_oqtopus_subcommand_output,
    stream_oqtopus_subcommand,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter(prefix="/backend", tags=["backend"])
logger = logging.getLogger(__name__)

_VALID_SERVICES = frozenset({
    "all",
    "core",
    "sse_engine",
    "mitigator",
    "estimator",
    "combiner",
    "tranqu",
    "gateway",
})
_VALID_COMPONENTS = frozenset({"engine", "tranqu", "gateway"})
_VALID_STATUSES = frozenset({"active", "inactive", "maintenance"})


def _build_args(  # noqa: C901, PLR0911, PLR0912, PLR0913, PLR0917
    cmd: str,
    service: str,
    component: str,
    version: str,
    foreground: bool,  # noqa: FBT001
    status: str,
    skip_sse_build: bool,  # noqa: FBT001
) -> list[str]:
    """Translate validated query params into oqtopus backend argv.

    Returns:
        List of string arguments to pass to the oqtopus backend CLI.

    Raises:
        ValueError: If an invalid service, component, status, or command is provided.

    """
    if cmd == "status":
        return ["status"]
    if cmd == "info":
        return ["info"]
    if cmd in {"start", "stop", "restart"}:
        if service not in _VALID_SERVICES:
            msg = f"Invalid service '{service}'"
            raise ValueError(msg)
        args = [cmd, service]
        if cmd == "start" and foreground:
            args.append("--foreground")
        return args
    if cmd == "versions":
        if component not in _VALID_COMPONENTS:
            msg = f"Invalid component '{component}'"
            raise ValueError(msg)
        return ["versions", component]
    if cmd == "install":
        comp = component if component in _VALID_COMPONENTS else None
        if comp is None and component != "all":
            msg = f"Invalid component '{component}'"
            raise ValueError(msg)
        args = ["install", component]
        if component != "all" and version:
            args.append(version)
        if skip_sse_build:
            args.append("--skip-sse-build")
        return args
    if cmd == "update":
        if component not in _VALID_COMPONENTS:
            msg = f"Invalid component '{component}'"
            raise ValueError(msg)
        return ["update", component]
    if cmd == "uninstall":
        if component not in _VALID_COMPONENTS:
            msg = f"Invalid component '{component}'"
            raise ValueError(msg)
        if not version:
            msg = "version is required for uninstall"
            raise ValueError(msg)
        return ["uninstall", component, version]
    if cmd == "build":
        return ["build", "sse-runtime"]
    if cmd == "device-status-show":
        return ["device-status", "show"]
    if cmd == "device-status-set":
        if status not in _VALID_STATUSES:
            msg = f"Invalid status '{status}'"
            raise ValueError(msg)
        return ["device-status", status]
    msg = f"Unknown command '{cmd}'"
    raise ValueError(msg)


@router.get(
    "/{name}/settings-partial",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.get")],
)
async def get_settings_partial(request: Request, name: str) -> HTMLResponse:
    """Return the settings partial HTML for the given environment.

    Returns:
        HTMLResponse with the settings partial template.

    """
    cfg = _get_config(request)
    env = _get_environment_or_404(name, cfg)
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    meta = _read_metadata(resolved)
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
    if component not in _VALID_COMPONENTS:
        raise HTTPException(status_code=400, detail=f"Invalid component '{component}'")

    cfg = _get_config(request)
    env = _get_environment_or_404(name, cfg)

    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    result = await run_oqtopus_subcommand_output(
        "backend", ["versions", component], cwd
    )
    if not result.ok:
        raise HTTPException(
            status_code=502,
            detail=result.stderr.strip()
            or f"oqtopus backend versions {component} failed",
        )

    versions = [
        m.group()
        for line in result.stdout.splitlines()
        if (m := re.search(r"branch:\S+|v\d+[\w.+-]*", line))
    ]
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
    env = _get_environment_or_404(name, cfg)

    try:
        backend_args = _build_args(
            cmd, service, component, version, foreground, status, skip_sse_build
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    """
    cfg = _get_config(request)
    env = _get_environment_or_404(name, cfg)
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    meta = _read_metadata(resolved)
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
