"""Server-side directory browser for the path picker."""

import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._utils import _get_config

router = APIRouter(prefix="/browse", tags=["browse"])


@router.get(
    "",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.create")],
)
async def browse(request: Request, path: str = "") -> HTMLResponse:
    """Return an HTML directory listing for the given path.

    Restricted to the configured environment base path and its subdirectories.
    Defaults to the base path when *path* is not provided or out of range.

    Returns:
        HTMLResponse with the directory listing.

    """
    cfg = _get_config(request)
    base = pathlib.Path(cfg.default_environment_base_path).resolve()  # noqa: ASYNC240
    current = pathlib.Path(path).resolve() if path else base  # noqa: ASYNC240

    try:
        # relative_to() raises ValueError when current is outside base
        current.relative_to(base)
    except ValueError:
        current = base

    if not current.is_dir():
        current = base

    try:
        entries = sorted(
            (e for e in current.iterdir() if e.is_dir() and not e.name.startswith(".")),
            key=lambda e: e.name,
        )
    except PermissionError, FileNotFoundError:
        entries = []

    # Hide the parent link at base so users cannot navigate above it
    parent = current.parent if current != base else None

    return request.app.state.templates.TemplateResponse(
        request,
        "browse/_picker.html",
        {"current": current, "entries": entries, "parent": parent},
    )
