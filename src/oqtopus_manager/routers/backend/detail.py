"""Backend detail HTML page, plus its /api/backend JSON/Server-Sent Events routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._problem import problem_response
from oqtopus_manager.routers._utils import (
    _get_config,
    _get_lock_registry,
    _get_templates,
)
from oqtopus_manager.services import backend as backend_service
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError

router = APIRouter(prefix="/backend", tags=["backend"])
api_router = APIRouter(prefix="/api/backend", tags=["backend-api"])
logger = logging.getLogger(__name__)


# ── HTML pages ───────────────────────────────────────────────────────────────


@router.get(
    "/{name}",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.get")],
)
async def get_environment(request: Request, name: str) -> HTMLResponse:
    """Render the environment detail page.

    Deliberately does not call ``info`` (a CLI subprocess): the page renders
    immediately from ``environments.yaml`` alone, and the client fetches
    ``GET /api/backend/{name}`` after load to fill in settings and toggle the
    ops sections, matching the list page's existing pattern.

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
    ctx: dict = {
        "env": env,
        "resolved_root_path": resolved,
        "components": backend_service.COMPONENTS,
    }
    return _get_templates(request).TemplateResponse(
        request, "environments/backend_detail.html", ctx
    )


# ── /api/backend JSON ────────────────────────────────────────────────────────


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
    "/{name}/locks",
    dependencies=[require_permission("environment.get")],
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
    dependencies=[require_permission("environment.locks.manage")],
)
async def force_unlock_lock(request: Request, name: str, scope: str) -> JSONResponse:  # ruff: ignore[unused-function-argument]
    """Forcibly clear one lock reported by ``GET .../locks``.

    Does not stop whatever CLI subprocess it belongs to -- see
    ``LockRegistry.force_unlock`` for the safety trade-off this accepts.

    Returns:
        JSONResponse ``{"ok": true}`` if *scope* was held (and is now
        cleared), ``{"ok": false}`` if nothing was held there.

    """
    freed = await _get_lock_registry(request).force_unlock(scope)
    return JSONResponse({"ok": freed})
