"""Shared helper functions for environment router modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

from oqtopus_manager.services.exceptions import LockConflictError, ServiceError

if TYPE_CHECKING:
    from fastapi import Request
    from fastapi.templating import Jinja2Templates

    from oqtopus_manager.config import AppConfig
    from oqtopus_manager.services.locks import LockRegistry


def _get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def _get_config(request: Request) -> AppConfig:
    return request.app.state.config


def _get_lock_registry(request: Request) -> LockRegistry:
    return request.app.state.lock_registry


def _get_held_by(request: Request) -> str:
    """Return the account name to attribute a newly-acquired lock to.

    Returns:
        ``request.state.user.account``, or "unknown" if unauthenticated
        (auth disabled, or no user resolved).

    """
    user = getattr(request.state, "user", None)
    return user.account if user is not None else "unknown"


def _lock_error_response(exc: ServiceError) -> JSONResponse:
    """Translate a file-lock ServiceError into the API's ``{"ok": false, ...}`` shape.

    Returns:
        JSONResponse matching the existing lock/save/release error contract.

    """
    if isinstance(exc, LockConflictError):
        return JSONResponse(
            {
                "ok": False,
                "locked_since": exc.locked_since,
                "locked_since_ts": exc.locked_since_ts,
            },
            status_code=exc.status_code,
        )
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status_code)
