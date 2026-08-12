"""Route handler for the /me (current user) page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/me", response_class=HTMLResponse)
async def me_page(request: Request) -> HTMLResponse:
    """Render the current user's account and role page.

    Returns:
        The rendered me.html template.

    """
    return request.app.state.templates.TemplateResponse(request, "me.html", {})
