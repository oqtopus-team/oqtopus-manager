"""Dedicated install/uninstall/update/versions routes for cloud-local environments."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from oqtopus_auth.fastapi import require_permission
from pydantic import BaseModel

from oqtopus_manager.routers._problem import problem_response
from oqtopus_manager.routers._utils import (
    _get_config,
    _get_held_by,
    _get_lock_registry,
    _sse_response,
)
from oqtopus_manager.services import cloud_local as cloud_local_service
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError
from oqtopus_manager.services.locks import no_lock

api_router = APIRouter(prefix="/api/cloud-local", tags=["cloud-local-api"])
logger = logging.getLogger(__name__)

_SUBCOMMAND = "cloud-local"


class _InstallBody(BaseModel):
    version: str = ""


class _UninstallBody(BaseModel):
    version: str


@api_router.get(
    "/{name}/components/{component}/versions",
    dependencies=[require_permission("environment.get")],
)
async def get_component_versions(
    request: Request, name: str, component: str
) -> JSONResponse:
    """Run ``oqtopus cloud-local versions <component>`` and return it as JSON.

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
    "/{name}/components/{component}/versions/stream",
    dependencies=[require_permission("environment.get")],
)
async def stream_component_versions(
    request: Request, name: str, component: str
) -> StreamingResponse:
    """Run ``oqtopus cloud-local versions <component>`` and stream raw output as SSE.

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
        args = cloud_local_service.build_component_args("versions", component, "")
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    logger.info("Cloud-local versions stream: args=%s env=%s", args, name)
    return _sse_response(_SUBCOMMAND, args, cwd, cfg, no_lock())


@api_router.post(
    "/{name}/components/{component}/install",
    dependencies=[require_permission("environment.component.manage")],
)
async def install_component(
    request: Request, name: str, component: str, body: _InstallBody
) -> StreamingResponse:
    """Run ``oqtopus cloud-local install <component>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found or the component is invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = cloud_local_service.build_component_args(
            "install", component, body.version
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Cloud-local install: args=%s env=%s", args, name)
    lock = cloud_local_service.install_lock_for(
        _get_lock_registry(request),
        name,
        component,
        operation=operation,
        held_by=_get_held_by(request),
    )
    return _sse_response(_SUBCOMMAND, args, cwd, cfg, lock)


@api_router.post(
    "/{name}/components/{component}/uninstall",
    dependencies=[require_permission("environment.component.manage")],
)
async def uninstall_component(
    request: Request, name: str, component: str, body: _UninstallBody
) -> StreamingResponse:
    """Run ``oqtopus cloud-local uninstall <component>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found, the component is
            invalid, or version is missing.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = cloud_local_service.build_component_args(
            "uninstall", component, body.version
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Cloud-local uninstall: args=%s env=%s", args, name)
    lock = cloud_local_service.component_lock_for(
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
)
async def update_component(
    request: Request, name: str, component: str
) -> StreamingResponse:
    """Run ``oqtopus cloud-local update <component>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found or the component is invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = cloud_local_service.build_component_args("update", component, "")
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Cloud-local update: args=%s env=%s", args, name)
    lock = cloud_local_service.component_lock_for(
        _get_lock_registry(request),
        name,
        component,
        operation=operation,
        held_by=_get_held_by(request),
    )
    return _sse_response(_SUBCOMMAND, args, cwd, cfg, lock)
