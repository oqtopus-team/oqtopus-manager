"""Debug route: exposes user information, request headers, and JWT payload."""

from __future__ import annotations

import base64
import fnmatch
import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from oqtopus_auth.header_provider import extract_token

router = APIRouter()

_JWT_PARTS_MIN = 2


def _decode_jwt_without_verification(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < _JWT_PARTS_MIN:
        return {"error": "Invalid JWT format"}

    header_b64, payload_b64 = parts[0], parts[1]

    def decode_part(value: str) -> dict:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded)
        return json.loads(raw)

    return {
        "header": decode_part(header_b64),
        "payload": decode_part(payload_b64),
    }


@router.get("/debug", response_class=HTMLResponse)
async def debug_page(request: Request) -> HTMLResponse:
    """Render request headers, mapped roles, and decoded JWT for debugging.

    Returns:
        The rendered debug.html template.

    """
    headers = sorted(request.headers.items())

    # Use the configured jwt_header so the debug view matches the auth provider
    header_cfg = request.app.state.config.auth.header
    jwt_header = header_cfg.jwt_header if header_cfg else "authorization"
    header_value = request.headers.get(jwt_header, "")
    token = extract_token(jwt_header, header_value)

    jwt_result: dict = {}
    if token:
        try:
            jwt_result = _decode_jwt_without_verification(token)
        except Exception as e:  # noqa: BLE001
            jwt_result = {"error": str(e)}

    # Compute allowed raw roles (after allow_raw_roles filtering) when patterns are set
    user = request.state.user
    allow_patterns = header_cfg.allow_raw_roles if header_cfg else []
    allowed_raw_roles: list[str] | None = None
    if user and allow_patterns:
        allowed_raw_roles = [
            raw_role
            for raw_role in user.raw_groups
            if any(fnmatch.fnmatch(raw_role, pat) for pat in allow_patterns)
        ]

    return request.app.state.templates.TemplateResponse(
        request,
        "debug.html",
        {
            "headers": headers,
            "jwt": jwt_result,
            "allowed_raw_roles": allowed_raw_roles,
        },
    )
