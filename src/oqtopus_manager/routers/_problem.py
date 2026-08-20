"""RFC 9457 (application/problem+json) error responses for the /api routes.

Only the new JSON read endpoints (status/device-status/info/versions/list)
use this shape; existing HTML/HTMX routes keep their current
``HTTPException(detail=...)`` contract unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from oqtopus_manager.services.environment import Outcome
    from oqtopus_manager.services.exceptions import ServiceError

_TITLES: dict[str, str] = {
    "CliNotFoundError": "oqtopus CLI not found",
    "CommandFailedError": "CLI command failed",
    "CliTimeoutError": "CLI command timed out",
    "EnvironmentNotFoundError": "Environment not found",
    "ReservedEnvironmentNameError": "Environment name is reserved",
    "EnvironmentAlreadyExistsError": "Environment already exists",
    "InvalidArgumentError": "Invalid argument",
}


class ProblemDetail(BaseModel):
    """RFC 9457 problem details body."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    returncode: int | None = None


def to_problem_detail(exc: ServiceError) -> ProblemDetail:
    """Map a ServiceError to its RFC 9457 representation.

    Returns:
        The ProblemDetail for this exception.

    """
    title = _TITLES.get(type(exc).__name__, exc.__class__.__name__)
    return ProblemDetail(
        title=title,
        status=exc.status_code,
        detail=str(exc),
        returncode=getattr(exc, "returncode", None),
    )


def serialize_outcome(outcome: Outcome | None) -> dict | None:
    """Serialize an environment.Outcome for the ``GET /api/{template}`` body.

    ``data``/``error`` are mutually exclusive, and the error shape omits
    ``status`` (redundant -- the whole response is 200).

    Returns:
        None if *outcome* is None (not fetched); otherwise ``{"data": ...}``
        or ``{"error": ...}``.

    Raises:
        RuntimeError: If *outcome* has neither data nor error (a construction
            bug in build_environment_list, never expected in practice).

    """
    if outcome is None:
        return None
    if outcome.error is not None:
        detail = to_problem_detail(outcome.error)
        return {
            "error": detail.model_dump(exclude_none=True, exclude={"status", "type"})
        }
    if outcome.data is None:
        msg = "Outcome has neither data nor error"
        raise RuntimeError(msg)
    return {"data": outcome.data.model_dump()}


def problem_response(exc: ServiceError) -> JSONResponse:
    """Build an ``application/problem+json`` response for a ServiceError.

    Returns:
        JSONResponse with the RFC 9457 problem body and matching status code.

    """
    body = to_problem_detail(exc)
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=exc.status_code,
        media_type="application/problem+json",
    )
