"""Dedicated install/uninstall/update/build/versions routes for backend environments.

install/update/build-sse-runtime are two-phase (POST answers 202 with an
operation ID; output is read from ``.../operations/{id}/stream``), since
they can take minutes. uninstall and versions are short and stream their
output directly in the POST/GET response.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from oqtopus_auth.fastapi import require_permission
from pydantic import BaseModel

from oqtopus_manager.routers._operations import (
    make_operations_router,
    start_operation,
)
from oqtopus_manager.routers._problem import problem_response
from oqtopus_manager.routers._utils import (
    _get_config,
    _get_held_by,
    _get_lock_registry,
    _sse_response,
)
from oqtopus_manager.services import backend as backend_service
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError
from oqtopus_manager.services.locks import no_lock

api_router = APIRouter(prefix="/api/backend", tags=["backend-api"])
logger = logging.getLogger(__name__)

_SUBCOMMAND = "backend"

operations_api_router = make_operations_router(
    "/api/backend", _SUBCOMMAND, ["backend-api"]
)


class _InstallBody(BaseModel):
    version: str = ""
    skip_sse_build: bool = False


class _UninstallBody(BaseModel):
    version: str


@api_router.get(
    "/{name}/components/{component}/versions",
    dependencies=[require_permission("environment.get")],
)
async def get_component_versions(
    request: Request, name: str, component: str
) -> JSONResponse:
    """Run ``oqtopus backend versions <component>`` and return it as JSON.

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


@api_router.get(
    "/{name}/components/{component}/versions/stream",
    dependencies=[require_permission("environment.get")],
)
async def stream_component_versions(
    request: Request, name: str, component: str
) -> StreamingResponse:
    """Run ``oqtopus backend versions <component>`` and stream raw output as SSE.

    Backs the console's raw-output display alongside the JSON versions
    endpoint above; read-only, so it needs no lock.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found or the component is invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = backend_service.build_versions_args(component)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    logger.info("Backend versions stream: args=%s env=%s", args, name)
    return _sse_response(_SUBCOMMAND, args, cwd, cfg, no_lock())


@api_router.post(
    "/{name}/components/{component}/install",
    dependencies=[require_permission("environment.component.manage")],
    status_code=202,
)
async def install_component(
    request: Request, name: str, component: str, body: _InstallBody
) -> JSONResponse:
    """Start ``oqtopus backend install <component>`` as a two-phase operation.

    Returns:
        202 with the operation ID (read its output from
        ``GET .../operations/{operation_id}/stream``), or an RFC 9457
        problem response.

    """
    try:
        args = backend_service.build_install_args(
            component, body.version, body.skip_sse_build
        )
    except ServiceError as exc:
        return problem_response(exc)
    logger.info("Backend install: args=%s env=%s", args, name)
    return await start_operation(
        request,
        template=_SUBCOMMAND,
        name=name,
        kind="install",
        component=component,
        version=None if component == "all" else body.version or "latest",
        args=args,
        lock_scope=backend_service.install_lock_scope(name, component),
    )


@api_router.post(
    "/{name}/components/{component}/uninstall",
    dependencies=[require_permission("environment.component.manage")],
)
async def uninstall_component(
    request: Request, name: str, component: str, body: _UninstallBody
) -> StreamingResponse:
    """Run ``oqtopus backend uninstall <component> <version>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found, the component is
            invalid, or version is missing.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = backend_service.build_uninstall_args(component, body.version)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Backend uninstall: args=%s env=%s", args, name)
    lock = backend_service.component_lock_for(
        _get_lock_registry(request),
        name,
        component,
        operation=operation,
        held_by=_get_held_by(request),
    )
    return _sse_response(_SUBCOMMAND, args, cwd, cfg, lock)


@api_router.post(
    "/{name}/components/{component}/update",
    dependencies=[require_permission("environment.component.manage")],
    status_code=202,
)
async def update_component(request: Request, name: str, component: str) -> JSONResponse:
    """Start ``oqtopus backend update <component>`` as a two-phase operation.

    Returns:
        202 with the operation ID, or an RFC 9457 problem response.

    """
    try:
        args = backend_service.build_update_args(component)
    except ServiceError as exc:
        return problem_response(exc)
    logger.info("Backend update: args=%s env=%s", args, name)
    return await start_operation(
        request,
        template=_SUBCOMMAND,
        name=name,
        kind="update",
        component=component,
        version=None,
        args=args,
        lock_scope=backend_service.update_lock_scope(name, component),
    )


@api_router.post(
    "/{name}/build-sse-runtime",
    dependencies=[require_permission("environment.component.manage")],
    status_code=202,
)
async def build_sse_runtime(request: Request, name: str) -> JSONResponse:
    """Start ``oqtopus backend build sse-runtime`` as a two-phase operation.

    Returns:
        202 with the operation ID, or an RFC 9457 problem response.

    """
    args = backend_service.build_build_sse_runtime_args()
    logger.info("Backend build: args=%s env=%s", args, name)
    return await start_operation(
        request,
        template=_SUBCOMMAND,
        name=name,
        kind="build-sse-runtime",
        component="engine",
        version=None,
        args=args,
        lock_scope=backend_service.build_sse_runtime_lock_scope(),
    )
