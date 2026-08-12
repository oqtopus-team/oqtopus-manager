"""Shared log route factory used by all environment template types."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable, Sequence

from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._utils import (
    _get_config,
    _get_environment_or_404,
    _get_templates,
)
from oqtopus_manager.util.cli import stream_log_tail


def make_log_router(
    url_prefix: str,
    tags: Sequence[str],
    get_log_file: Callable[[pathlib.Path, str], pathlib.Path | None],
) -> APIRouter:
    """Return an APIRouter with all service log routes wired to ``url_prefix``.

    ``get_log_file`` resolves the log path from the environment root and service
    name; the implementation differs per template type (backend reads from YAML,
    cloud-local uses a fixed path).

    Returns:
        APIRouter with service log view, stream, and download routes.

    """
    router = APIRouter(prefix=url_prefix, tags=tags)  # type: ignore[arg-type]

    @router.get(
        "/{name}/services/{service}/log",
        response_class=HTMLResponse,
        dependencies=[require_permission("environment.log.get")],
    )
    async def service_log(request: Request, name: str, service: str) -> HTMLResponse:
        cfg = _get_config(request)
        env = _get_environment_or_404(name, cfg)
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        log_file = get_log_file(resolved, service)
        return _get_templates(request).TemplateResponse(
            request,
            "environments/service_log.html",
            {
                "env": env,
                "service": service,
                "url_prefix": url_prefix,
                "log_file": log_file,
                "buffer_lines": cfg.log_buffer_lines,
            },
        )

    @router.get(
        "/{name}/services/{service}/log/stream",
        dependencies=[require_permission("environment.log.get")],
    )
    async def service_log_stream(
        request: Request, name: str, service: str
    ) -> StreamingResponse:
        cfg = _get_config(request)
        env = _get_environment_or_404(name, cfg)
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        log_file = get_log_file(resolved, service)
        if log_file is None or not log_file.exists():
            raise HTTPException(status_code=404, detail="Log file not found.")
        return StreamingResponse(
            stream_log_tail(log_file, cfg.log_tail_lines),
            media_type="text/event-stream",
        )

    @router.get(
        "/{name}/services/{service}/log/download",
        dependencies=[require_permission("environment.log.get")],
    )
    async def service_log_download(
        request: Request, name: str, service: str
    ) -> FileResponse:
        cfg = _get_config(request)
        env = _get_environment_or_404(name, cfg)
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        log_file = get_log_file(resolved, service)
        if log_file is None or not log_file.exists():
            raise HTTPException(status_code=404, detail="Log file not found.")
        return FileResponse(
            path=log_file,
            filename=log_file.name,
            media_type="text/plain",
        )

    return router
