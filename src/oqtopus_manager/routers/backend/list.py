"""Routes for listing, creating, and deleting backend environments."""

from __future__ import annotations

import logging
import pathlib
import shutil
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from oqtopus_auth.fastapi import require_permission
from pydantic import ValidationError

from oqtopus_manager.models.environment import Environment
from oqtopus_manager.routers._utils import _get_config, _get_templates, _has_running_services
from oqtopus_manager.routers.backend._utils import _build_list_context
from oqtopus_manager.util.cli import stream_oqtopus_init

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter(prefix="/backend", tags=["backend"])
logger = logging.getLogger(__name__)


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
        _build_list_context(environments, cfg),
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


@router.post(
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
    proceed to open the SSE stream.  Returns an error JSON with the
    appropriate HTTP status on failure.

    Returns:
        JSONResponse indicating success or failure.

    """
    cfg = _get_config(request)
    environments = cfg.load_environments()

    if any(e.name == name for e in environments):
        return JSONResponse(
            {"ok": False, "error": f"Environment '{name}' already exists."},
            status_code=409,
        )

    try:
        Environment(
            name=name,
            template=template,
            root_path=pathlib.Path(root_path) if root_path.strip() else None,
        )
    except ValidationError as exc:
        return JSONResponse(
            {"ok": False, "error": exc.errors()[0]["msg"]},
            status_code=422,
        )

    logger.info("Environment '%s' validated (template=%s)", name, template)
    return JSONResponse({"ok": True})


@router.get(
    "/stream",
    dependencies=[require_permission("environment.create")],
)
async def stream_environment_init(
    request: Request,
    name: str,
    template: str,
    root_path: str = "",
) -> StreamingResponse:
    """SSE endpoint: run oqtopus init and stream output line by line.

    Returns:
        StreamingResponse with SSE-formatted output.

    """
    cfg = _get_config(request)

    new_env = Environment(
        name=name,
        template=template,
        root_path=pathlib.Path(root_path) if root_path.strip() else None,
    )
    parent_dir = new_env.resolved_root_path(cfg.default_environment_base_path).parent
    parent_dir.mkdir(parents=True, exist_ok=True)

    async def event_stream() -> AsyncGenerator[str]:
        success = False
        async for chunk in stream_oqtopus_init(
            name=name, template=template, cwd=parent_dir
        ):
            yield chunk
            if "event: done\ndata: success" in chunk:
                success = True

        if success:
            environments = cfg.load_environments()
            # Guard against duplicate entries if config was modified concurrently
            if not any(e.name == name for e in environments):
                resolved = new_env.resolved_root_path(cfg.default_environment_base_path)
                # Persist absolute path so the entry is cwd-independent
                env_to_save = new_env.model_copy(update={"root_path": resolved})
                environments.append(env_to_save)
                cfg.save_environments(environments)
                logger.info("Environment '%s' created and saved to config", name)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete(
    "/{name}",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.delete")],
)
async def delete_environment(request: Request, name: str) -> HTMLResponse:
    """Delete an environment and its directory.

    Returns:
        HTMLResponse with the updated environments list.

    Raises:
        HTTPException: If the environment is not found.

    """
    cfg = _get_config(request)
    environments = cfg.load_environments()
    target = next((e for e in environments if e.name == name), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Environment '{name}' not found.")

    root_dir = target.resolved_root_path(cfg.default_environment_base_path)
    if root_dir.exists() and await _has_running_services("backend", root_dir):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete '{name}': one or more services are still running. "
                "Stop all services first."
            ),
        )

    logger.info("Deleting environment '%s' (root=%s)", name, root_dir)
    if root_dir.exists():
        shutil.rmtree(root_dir)
        logger.info("Deleted directory: %s", root_dir)

    remaining = [e for e in environments if e.name != name]
    cfg.save_environments(remaining)

    backend_remaining = [e for e in remaining if e.template == "backend"]
    return _get_templates(request).TemplateResponse(
        request,
        "environments/list.html",
        _build_list_context(backend_remaining, cfg),
    )
