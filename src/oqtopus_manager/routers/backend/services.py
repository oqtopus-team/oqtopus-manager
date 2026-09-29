"""Dedicated start/stop/restart/device-status routes for backend environments."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from oqtopus_auth.fastapi import require_permission
from pydantic import BaseModel

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

if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse

api_router = APIRouter(prefix="/api/backend", tags=["backend-api"])
logger = logging.getLogger(__name__)

_SUBCOMMAND = "backend"


class _StartBody(BaseModel):
    foreground: bool = False


class _DeviceStatusBody(BaseModel):
    status: str


@api_router.post(
    "/{name}/services/{service}/start",
    dependencies=[require_permission("environment.service.control")],
)
async def start_service(
    request: Request, name: str, service: str, body: _StartBody
) -> StreamingResponse:
    """Run ``oqtopus backend start <service>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found or the service is invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = backend_service.build_service_args("start", service, body.foreground)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Backend service start: args=%s env=%s", args, name)
    lock = backend_service.service_lock_for(
        _get_lock_registry(request),
        name,
        service,
        operation=operation,
        held_by=_get_held_by(request),
    )
    return _sse_response(_SUBCOMMAND, args, cwd, cfg, lock)


@api_router.post(
    "/{name}/services/{service}/stop",
    dependencies=[require_permission("environment.service.control")],
)
async def stop_service(request: Request, name: str, service: str) -> StreamingResponse:
    """Run ``oqtopus backend stop <service>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found or the service is invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = backend_service.build_service_args("stop", service, foreground=False)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Backend service stop: args=%s env=%s", args, name)
    lock = backend_service.service_lock_for(
        _get_lock_registry(request),
        name,
        service,
        operation=operation,
        held_by=_get_held_by(request),
    )
    return _sse_response(_SUBCOMMAND, args, cwd, cfg, lock)


@api_router.post(
    "/{name}/services/{service}/restart",
    dependencies=[require_permission("environment.service.control")],
)
async def restart_service(
    request: Request, name: str, service: str
) -> StreamingResponse:
    """Run ``oqtopus backend restart <service>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found or the service is invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = backend_service.build_service_args("restart", service, foreground=False)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Backend service restart: args=%s env=%s", args, name)
    lock = backend_service.service_lock_for(
        _get_lock_registry(request),
        name,
        service,
        operation=operation,
        held_by=_get_held_by(request),
    )
    return _sse_response(_SUBCOMMAND, args, cwd, cfg, lock)


@api_router.post(
    "/{name}/device-status",
    dependencies=[require_permission("environment.service.control")],
)
async def set_device_status(
    request: Request, name: str, body: _DeviceStatusBody
) -> StreamingResponse:
    """Run ``oqtopus backend device-status <status>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found or the status is invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = backend_service.build_device_status_args(body.status)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    logger.info("Backend device-status: args=%s env=%s", args, name)
    return _sse_response(_SUBCOMMAND, args, cwd, cfg, no_lock())
