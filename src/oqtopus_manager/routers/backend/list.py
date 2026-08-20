"""Routes for listing, creating, and deleting backend environments."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._problem import serialize_outcome
from oqtopus_manager.routers._utils import _get_config, _get_templates
from oqtopus_manager.services import backend as backend_service
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter(prefix="/backend", tags=["backend"])
api_router = APIRouter(prefix="/api/backend", tags=["backend-api"])
logger = logging.getLogger(__name__)


# ── HTML pages ───────────────────────────────────────────────────────────────


@router.get(
    "",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.get")],
)
async def list_environments(request: Request) -> HTMLResponse:
    """Render the environments list page.

    Returns:
        HTMLResponse with the rendered environments list.

    """
    cfg = _get_config(request)
    all_envs = cfg.load_environments()
    environments = [e for e in all_envs if e.template == "backend"]
    return _get_templates(request).TemplateResponse(
        request,
        "environments/list.html",
        backend_service.build_list_context(environments, cfg),
    )


@router.get(
    "/new",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.create")],
)
async def new_environment_form(request: Request) -> HTMLResponse:
    """Render the new environment form.

    Returns:
        HTMLResponse with the rendered new environment form.

    """
    cfg = _get_config(request)
    return _get_templates(request).TemplateResponse(
        request,
        "environments/new.html",
        {
            "default_browse_path": cfg.default_environment_base_path,
            "default_template": "backend",
        },
    )


# ── /api/backend ─────────────────────────────────────────────────────────────


@api_router.get(
    "",
    dependencies=[require_permission("environment.get")],
)
async def list_environments_json(
    request: Request,
    include_status: bool = False,  # noqa: FBT001, FBT002
) -> JSONResponse:
    """Return every backend environment's info (and optionally status).

    Per-environment failures are reported inline rather than failing the
    whole request.

    Returns:
        JSONResponse with the aggregated environment list.

    """
    cfg = _get_config(request)
    environments = [e for e in cfg.load_environments() if e.template == "backend"]
    raw = await env_service.build_environment_list(
        cfg,
        environments,
        template="backend",
        include_status=include_status,
        has_device_status=True,
        get_info=backend_service.get_info,
        get_status=backend_service.get_status,
        get_device_status=backend_service.get_device_status,
    )
    body = {
        "template": raw["template"],
        "environments": [
            {
                "name": e["name"],
                "environment": serialize_outcome(e["environment"]),
                "status": serialize_outcome(e["status"]),
                "device_status": serialize_outcome(e["device_status"]),
            }
            for e in raw["environments"]
        ],
    }
    return JSONResponse(body)


@api_router.post(
    "",
    dependencies=[require_permission("environment.create")],
)
async def create_environment(
    request: Request,
    name: Annotated[str, Form()],
    template: Annotated[str, Form()],
    root_path: Annotated[str, Form()] = "",
) -> JSONResponse:
    """Validate the new environment request and return JSON.

    Returns ``{"ok": true}`` when validation passes so the client can
    proceed to open the Server-Sent Events stream.  Returns an error JSON
    with the appropriate HTTP status on failure.

    Returns:
        JSONResponse indicating success or failure.

    """
    cfg = _get_config(request)
    try:
        env_service.validate_new_environment(cfg, name, template, root_path)
    except ServiceError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)}, status_code=exc.status_code
        )
    logger.info("Environment '%s' validated (template=%s)", name, template)
    return JSONResponse({"ok": True})


@api_router.get(
    "/stream",
    dependencies=[require_permission("environment.create")],
)
async def stream_environment_init(
    request: Request,
    name: str,
    template: str,
    root_path: str = "",
) -> StreamingResponse:
    """Server-Sent Events endpoint: run oqtopus init and stream output line by line.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    """
    cfg = _get_config(request)

    async def event_stream() -> AsyncGenerator[str]:
        async for chunk in env_service.stream_environment_init(
            cfg, name, template, root_path
        ):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@api_router.delete(
    "/{name}",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.delete")],
)
async def delete_environment(request: Request, name: str) -> HTMLResponse:
    """Delete an environment and its directory.

    Still returns the re-rendered HTML list (HTMX swaps it in), unlike every
    other /api route: this is a deliberate exception, kept only because the
    HTML delete button still depends on it.

    Returns:
        HTMLResponse with the updated environments list.

    Raises:
        HTTPException: If the environment is not found.

    """
    cfg = _get_config(request)
    try:
        await env_service.delete_environment(cfg, name, "backend")
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    remaining = [e for e in cfg.load_environments() if e.template == "backend"]
    return _get_templates(request).TemplateResponse(
        request,
        "environments/list.html",
        backend_service.build_list_context(remaining, cfg),
    )
