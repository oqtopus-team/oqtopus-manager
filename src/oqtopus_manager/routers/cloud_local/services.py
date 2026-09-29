"""Dedicated start/stop/restart routes for cloud-local environments."""

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
from oqtopus_manager.services import cloud_local as cloud_local_service
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError

if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse

api_router = APIRouter(prefix="/api/cloud-local", tags=["cloud-local-api"])
logger = logging.getLogger(__name__)

_SUBCOMMAND = "cloud-local"


class _StartBody(BaseModel):
    foreground: bool = False


@api_router.post(
    "/{name}/services/{service}/start",
    dependencies=[require_permission("environment.service.control")],
)
async def start_service(
    request: Request, name: str, service: str, body: _StartBody
) -> StreamingResponse:
    """Run ``oqtopus cloud-local start <service>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found or the service is invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = cloud_local_service.build_service_args("start", service, body.foreground)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Cloud-local service start: args=%s env=%s", args, name)
    lock = cloud_local_service.service_lock_for(
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
    """Run ``oqtopus cloud-local stop <service>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found or the service is invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = cloud_local_service.build_service_args("stop", service, foreground=False)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Cloud-local service stop: args=%s env=%s", args, name)
    lock = cloud_local_service.service_lock_for(
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
    """Run ``oqtopus cloud-local restart <service>`` and stream output as SSE.

    Returns:
        StreamingResponse with Server-Sent Events-formatted output.

    Raises:
        HTTPException: If the environment is not found or the service is invalid.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        args = cloud_local_service.build_service_args(
            "restart", service, foreground=False
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    operation = f"{' '.join(args)} (environment={name})"
    logger.info("Cloud-local service restart: args=%s env=%s", args, name)
    lock = cloud_local_service.service_lock_for(
        _get_lock_registry(request),
        name,
        service,
        operation=operation,
        held_by=_get_held_by(request),
    )
    return _sse_response(_SUBCOMMAND, args, cwd, cfg, lock)
