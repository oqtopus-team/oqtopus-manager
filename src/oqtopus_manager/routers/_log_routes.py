"""Shared log route factory used by all environment template types."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable, Sequence

from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._utils import _get_config, _get_templates
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import ServiceError
from oqtopus_manager.util.cli import stream_log_tail


def make_log_router(
    html_url_prefix: str,
    api_url_prefix: str,
    tags: Sequence[str],
    get_log_file: Callable[[pathlib.Path, str], pathlib.Path | None],
) -> tuple[APIRouter, APIRouter]:
    """Return the HTML and /api routers for the service log viewer.

    ``get_log_file`` resolves the log path from the environment root and service
    name; the implementation differs per template type (backend reads from YAML,
    cloud-local uses a fixed path).

    Args:
        html_url_prefix: URL prefix for the HTML page (e.g. "/backend").
        api_url_prefix: URL prefix for the stream/download endpoints
            (e.g. "/api/backend").
        tags: OpenAPI tags for both routers.
        get_log_file: Resolves a service's log file path.

    Returns:
        (router, api_router): HTML page router and stream/download router.

    """
    router = APIRouter(prefix=html_url_prefix, tags=tags)  # type: ignore[arg-type]
    api_router = APIRouter(prefix=api_url_prefix, tags=tags)  # type: ignore[arg-type]

    @router.get(
        "/{name}/services/{service}/log",
        response_class=HTMLResponse,
        dependencies=[require_permission("environment.log.get")],
    )
    async def service_log(request: Request, name: str, service: str) -> HTMLResponse:
        cfg = _get_config(request)
        try:
            env = env_service.get_environment_or_404(name, cfg)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        log_file = get_log_file(resolved, service)
        return _get_templates(request).TemplateResponse(
            request,
            "environments/service_log.html",
            {
                "env": env,
                "service": service,
                "url_prefix": html_url_prefix,
                "api_url_prefix": api_url_prefix,
                "log_file": log_file,
                "buffer_lines": cfg.log_buffer_lines,
            },
        )

    @api_router.get(
        "/{name}/services/{service}/log/stream",
        dependencies=[require_permission("environment.log.get")],
    )
    async def service_log_stream(
        request: Request, name: str, service: str
    ) -> StreamingResponse:
        cfg = _get_config(request)
        try:
            env = env_service.get_environment_or_404(name, cfg)
            resolved = env.resolved_root_path(cfg.default_environment_base_path)
            log_file = env_service.resolve_existing_log_file(
                get_log_file, resolved, service
            )
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return StreamingResponse(
            stream_log_tail(log_file, cfg.log_tail_lines),
            media_type="text/event-stream",
        )

    @api_router.get(
        "/{name}/services/{service}/log/download",
        dependencies=[require_permission("environment.log.get")],
    )
    async def service_log_download(
        request: Request, name: str, service: str
    ) -> FileResponse:
        cfg = _get_config(request)
        try:
            env = env_service.get_environment_or_404(name, cfg)
            resolved = env.resolved_root_path(cfg.default_environment_base_path)
            log_file = env_service.resolve_existing_log_file(
                get_log_file, resolved, service
            )
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return FileResponse(
            path=log_file,
            filename=log_file.name,
            media_type="text/plain",
        )

    return router, api_router
