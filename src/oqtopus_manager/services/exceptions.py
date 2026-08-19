"""Domain exceptions raised by the services layer.

The services layer has no FastAPI dependency and never constructs an HTTP
response itself. Each exception carries a ``status_code`` that is the
*default* mapping a router should use when it has no reason to do
otherwise (e.g. a plain ``HTTPException(status_code=exc.status_code,
detail=str(exc))``); routers remain free to catch a specific exception and
build a different response shape when the existing API contract requires
it (e.g. file-lock conflicts, which return ``{"ok": false, ...}`` rather
than the default ``{"detail": ...}`` shape).
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for errors raised by the services layer."""

    status_code: int = 500


class EnvironmentNotFoundError(ServiceError):
    """No environment with the given name is registered."""

    status_code = 404

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Environment '{name}' not found.")


class EnvironmentAlreadyExistsError(ServiceError):
    """An environment with the given name is already registered."""

    status_code = 409

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Environment '{name}' already exists.")


class EnvironmentValidationError(ServiceError):
    """The requested environment name/template/root_path failed validation."""

    status_code = 422


class ServicesStillRunningError(ServiceError):
    """The environment cannot be deleted while services are still running."""

    status_code = 409

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"Cannot delete '{name}': one or more services are still running. "
            "Stop all services first."
        )


class InvalidArgumentError(ServiceError):
    """An unknown command/service/component/status/config-type argument."""

    status_code = 400


class CommandFailedError(ServiceError):
    """An ``oqtopus`` CLI invocation exited non-zero."""

    status_code = 502


class TopologyNotConfiguredError(ServiceError):
    """``device_topology_json_path`` is not configured in the gateway config."""

    status_code = 404


class LogFileNotFoundError(ServiceError):
    """The resolved service log file does not exist."""

    status_code = 404


class LockConflictError(ServiceError):
    """The file is already locked by someone else."""

    status_code = 409

    def __init__(self, locked_since: str | None, locked_since_ts: float | None) -> None:
        self.locked_since = locked_since
        self.locked_since_ts = locked_since_ts
        super().__init__("File is already locked.")


class LockNotHeldError(ServiceError):
    """Release was attempted but the lock isn't held (or the token doesn't match)."""

    status_code = 403

    def __init__(self) -> None:
        super().__init__("Lock not held or token mismatch.")


class LockExpiredError(ServiceError):
    """Save was attempted but the lock had already expired."""

    status_code = 409

    def __init__(self) -> None:
        super().__init__("Lock expired.")


class LockTokenMismatchError(ServiceError):
    """Save was attempted with a token that doesn't match the held lock."""

    status_code = 403

    def __init__(self) -> None:
        super().__init__("Invalid token.")
