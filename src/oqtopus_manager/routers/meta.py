"""App-level meta routes: version, icon, favicon, and API docs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter()


@router.get("/version")
async def version(request: Request) -> dict[str, str]:
    """Return the application version.

    Returns:
        Dict with a single ``version`` key.

    """
    return {"version": request.app.version}


@router.get("/app-icon")
async def app_icon_file(request: Request) -> FileResponse:
    """Serve the operator-configured app icon.

    Returns:
        The icon file.

    Raises:
        HTTPException: If no icon is configured or the file is missing.

    """
    icon_path = request.app.state.config.app_icon_path
    if icon_path and icon_path.exists():
        return FileResponse(path=icon_path)
    raise HTTPException(status_code=404, detail="No app icon configured.")


@router.get("/favicon.ico", include_in_schema=False)
async def favicon(request: Request) -> FileResponse:
    """Serve the operator-configured favicon.

    Returns:
        The favicon file.

    Raises:
        HTTPException: If no favicon is configured or the file is missing.

    """
    fav_path = request.app.state.config.favicon_path
    if fav_path and fav_path.exists():
        return FileResponse(path=fav_path)
    raise HTTPException(status_code=404, detail="No favicon configured.")


@router.get("/api-docs", response_class=HTMLResponse)
async def api_docs_page(request: Request) -> HTMLResponse:
    """Render the API documentation page.

    Returns:
        HTMLResponse with the rendered API docs page.

    """
    return request.app.state.templates.TemplateResponse(request, "api_docs.html", {})
