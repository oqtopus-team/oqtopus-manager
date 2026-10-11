"""Two-phase operation routes shared by all environment template types.

``POST .../install`` (and update/build-sse-runtime) only starts the
operation and answers 202 with its ID; the output is read separately, any
number of times, from ``GET .../operations/{operation_id}/stream``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._problem import problem_response
from oqtopus_manager.routers._utils import (
    _get_config,
    _get_held_by,
    _get_lock_registry,
    _get_operation_registry,
)
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError
from oqtopus_manager.services.operations import OperationKey, OperationRequest

if TYPE_CHECKING:
    from collections.abc import Sequence

    from oqtopus_manager.services.locks import LockScope


async def start_operation(  # ruff: ignore[too-many-arguments]
    request: Request,
    *,
    template: str,
    name: str,
    kind: str,
    component: str,
    version: str | None,
    args: list[str],
    lock_scope: LockScope,
) -> JSONResponse:
    """Start ``oqtopus <template> <args>`` as a detached operation.

    An identical request (same environment and same arguments) that is
    still running is joined rather than started twice; anything else
    contending for the same locks is rejected with 409.

    Returns:
        202 with ``{"operation_id", "already_running"}``, or an RFC 9457
        problem response (404 environment, 409 lock conflict).

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
    except ServiceError as exc:
        return problem_response(exc)
    operation_request = OperationRequest(
        key=OperationKey(template=template, environment=name, args=tuple(args)),
        kind=kind,
        component=component,
        version=version,
        argv=["oqtopus", template, *args],
        cwd=env.resolved_root_path(cfg.default_environment_base_path),
        timeout=cfg.oqtopus_cli_operation_timeout_sec,
        lock_scope=lock_scope,
        label=f"{' '.join(args)} (environment={name})",
        started_by=_get_held_by(request),
    )
    try:
        operation, already_running = await _get_operation_registry(request).start(
            operation_request, _get_lock_registry(request)
        )
    except ServiceError as exc:
        return problem_response(exc)
    return JSONResponse(
        {"operation_id": operation.id, "already_running": already_running},
        status_code=202,
    )


def locks_response(request: Request) -> JSONResponse:
    """Return every held lock, with the running operation holding it (if any).

    Returns:
        JSONResponse with a list of
        {scope, operation, held_by, held_since, operation_id}.

    """
    operations = _get_operation_registry(request)
    rows = []
    for info in _get_lock_registry(request).snapshot():
        holder = operations.holder_of(info)
        rows.append({
            "scope": info.scope,
            "operation": info.operation,
            "held_by": info.held_by,
            "held_since": info.held_since,
            "operation_id": holder.id if holder is not None else None,
        })
    return JSONResponse(rows)


async def force_unlock_response(request: Request, scope: str) -> JSONResponse:
    """Force-unlock *scope*, flagging the running operation that held it.

    The operation keeps running (the subprocess is never stopped), but is
    marked ``lock_force_released`` so anyone watching it can see that
    something else may now run concurrently with it.

    Returns:
        JSONResponse ``{"ok": true}`` if *scope* was held (and is now
        cleared), ``{"ok": false}`` if nothing was held there.

    """
    locks = _get_lock_registry(request)
    holder = next(
        (
            _get_operation_registry(request).holder_of(info)
            for info in locks.snapshot()
            if info.scope == scope
        ),
        None,
    )
    freed = await locks.force_unlock(scope)
    if freed and holder is not None:
        holder.lock_force_released = True
    return JSONResponse({"ok": freed})


def make_operations_router(
    api_url_prefix: str, template: str, tags: Sequence[str]
) -> APIRouter:
    """Return the router listing and streaming one template's operations.

    Args:
        api_url_prefix: URL prefix (e.g. "/api/backend").
        template: The environment template (e.g. "backend"), matching the
            ``template`` the operations were started with.
        tags: OpenAPI tags.

    Returns:
        The APIRouter.

    """
    api_router = APIRouter(prefix=api_url_prefix, tags=list(tags))

    @api_router.get(
        "/{name}/operations",
        dependencies=[require_permission("environment.component.manage")],
    )
    async def list_operations(request: Request, name: str) -> JSONResponse:
        """List this environment's running and recently finished operations.

        Returns:
            JSONResponse with a list of operations, newest first, or an RFC
            9457 problem response if the environment is not found.

        """
        try:
            env_service.get_environment_or_404(name, _get_config(request))
        except ServiceError as exc:
            return problem_response(exc)
        operations = _get_operation_registry(request).list_for(
            template=template, environment=name
        )
        return JSONResponse([op.to_dict() for op in operations])

    @api_router.get(
        "/{name}/operations/{operation_id}/stream",
        dependencies=[require_permission("environment.component.manage")],
        response_model=None,
    )
    async def stream_operation(
        request: Request, name: str, operation_id: str
    ) -> StreamingResponse | JSONResponse:
        """Replay an operation's buffered output, then follow it live as SSE.

        Returns:
            StreamingResponse ending with ``event: done``, or an RFC 9457
            problem response if the operation is unknown or expired.

        """
        try:
            operation = _get_operation_registry(request).get(
                operation_id, template=template, environment=name
            )
        except ServiceError as exc:
            return problem_response(exc)
        return StreamingResponse(operation.subscribe(), media_type="text/event-stream")

    return api_router
