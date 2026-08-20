"""Routes for backend service config editor and gateway topology JSON."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._file_edit import (  # noqa: TC001 (pydantic body models FastAPI needs at runtime)
    _SaveBody,
    _UnlockBody,
)
from oqtopus_manager.routers._utils import (
    _get_config,
    _get_templates,
    _lock_error_response,
)
from oqtopus_manager.services import backend as backend_service
from oqtopus_manager.services import environment as env_service
from oqtopus_manager.services.exceptions import (
    LockConflictError,
    LockExpiredError,
    LockNotHeldError,
    LockTokenMismatchError,
    ServiceError,
)

router = APIRouter(prefix="/backend", tags=["backend"])
api_router = APIRouter(prefix="/api/backend", tags=["backend-api"])


@router.get(
    "/{name}/services/{service}/config",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.config.get")],
)
async def service_config(request: Request, name: str, service: str) -> HTMLResponse:
    """Render the service config editor page.

    Returns:
        HTMLResponse with the service config editor.

    Raises:
        HTTPException: If the environment is not found.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    config_dir = resolved / "config" / service

    config_path = config_dir / "config.yaml"
    logging_path = config_dir / "logging.yaml"
    config_state = env_service.check_lock(
        config_dir / "config.yaml.lock", cfg.file_edit_lock_timeout_sec
    )
    logging_state = env_service.check_lock(
        config_dir / "logging.yaml.lock", cfg.file_edit_lock_timeout_sec
    )

    return _get_templates(request).TemplateResponse(
        request,
        "environments/service_config.html",
        {
            "env": env,
            "service": service,
            "config_dir": config_dir,
            "config_content": (
                config_path.read_text(encoding="utf-8")
                if config_path.exists()
                else None
            ),
            "logging_content": (
                logging_path.read_text(encoding="utf-8")
                if logging_path.exists()
                else None
            ),
            "config_is_locked": config_state.is_locked,
            "config_locked_since": config_state.locked_since,
            "config_locked_since_ts": config_state.locked_since_ts,
            "logging_is_locked": logging_state.is_locked,
            "logging_locked_since": logging_state.locked_since,
            "logging_locked_since_ts": logging_state.locked_since_ts,
            **backend_service.load_topology_context(
                service, resolved, cfg.file_edit_lock_timeout_sec
            ),
            "lock_timeout_sec": cfg.file_edit_lock_timeout_sec,
        },
    )


@api_router.post(
    "/{name}/services/{service}/config/{which}/force-unlock",
    dependencies=[require_permission("environment.config.update")],
)
async def force_unlock_service_config(
    request: Request, name: str, service: str, which: str
) -> JSONResponse:
    """Force-unlock a service config file.

    Returns:
        JSONResponse with ok=True on success.

    Raises:
        HTTPException: If the environment or config type is not found/recognized.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        filename = backend_service.config_which_to_filename(which)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    env_service.force_unlock_file(resolved / "config" / service / f"{filename}.lock")
    return JSONResponse({"ok": True})


@api_router.post(
    "/{name}/services/{service}/config/{which}/lock",
    dependencies=[require_permission("environment.config.update")],
)
async def acquire_service_config_lock(
    request: Request, name: str, service: str, which: str
) -> JSONResponse:
    """Acquire a lock on a service config file.

    Returns:
        JSONResponse with ok=True and token on success, or conflict info.

    Raises:
        HTTPException: If the environment or config type is not found/recognized.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        filename = backend_service.config_which_to_filename(which)
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        lock_path = resolved / "config" / service / f"{filename}.lock"
        lock = env_service.acquire_file_lock(lock_path, cfg.file_edit_lock_timeout_sec)
    except LockConflictError as exc:
        return _lock_error_response(exc)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse({
        "ok": True,
        "token": lock.token,
        "acquired_ts": lock.acquired_ts,
    })


@api_router.post(
    "/{name}/services/{service}/config/{which}/unlock",
    dependencies=[require_permission("environment.config.update")],
)
async def release_service_config_lock(
    request: Request, name: str, service: str, which: str, body: _UnlockBody
) -> JSONResponse:
    """Release a lock on a service config file.

    Returns:
        JSONResponse with ok=True if the lock was released.

    Raises:
        HTTPException: If the environment or config type is not found/recognized.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        filename = backend_service.config_which_to_filename(which)
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        lock_path = resolved / "config" / service / f"{filename}.lock"
        env_service.release_file_lock(
            lock_path, body.token, cfg.file_edit_lock_timeout_sec
        )
    except LockNotHeldError as exc:
        return _lock_error_response(exc)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse({"ok": True})


@api_router.post(
    "/{name}/services/{service}/config/{which}/save",
    dependencies=[require_permission("environment.config.update")],
)
async def save_service_config(
    request: Request, name: str, service: str, which: str, body: _SaveBody
) -> JSONResponse:
    """Save a service config file after validating the lock token.

    Returns:
        JSONResponse with ok=True on success.

    Raises:
        HTTPException: If the environment or config type is not found/recognized.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        filename = backend_service.config_which_to_filename(which)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    config_path = resolved / "config" / service / filename
    lock_path = resolved / "config" / service / f"{filename}.lock"
    try:
        env_service.save_file(
            config_path,
            lock_path,
            body.content,
            body.token,
            cfg.file_edit_lock_timeout_sec,
        )
    except (LockExpiredError, LockTokenMismatchError) as exc:
        return _lock_error_response(exc)
    return JSONResponse({"ok": True})


@api_router.post(
    "/{name}/services/gateway/topology-json/force-unlock",
    dependencies=[require_permission("environment.config.update")],
)
async def force_unlock_gateway_topology_json(
    request: Request, name: str
) -> JSONResponse:
    """Force-unlock the gateway device topology JSON file.

    Returns:
        JSONResponse with ok=True on success.

    Raises:
        HTTPException: If the environment is not found or topology is not configured.

    """
    cfg = _get_config(request)
    try:
        path = backend_service.resolve_topology_path(name, cfg)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    env_service.force_unlock_file(path.parent / f"{path.name}.lock")
    return JSONResponse({"ok": True})


@api_router.post(
    "/{name}/services/gateway/topology-json/lock",
    dependencies=[require_permission("environment.config.update")],
)
async def acquire_gateway_topology_json_lock(
    request: Request, name: str
) -> JSONResponse:
    """Acquire a lock on the gateway device topology JSON file.

    Returns:
        JSONResponse with ok=True and token on success, or conflict info.

    Raises:
        HTTPException: If the environment is not found or topology is not configured.

    """
    cfg = _get_config(request)
    try:
        path = backend_service.resolve_topology_path(name, cfg)
        lock_path = path.parent / f"{path.name}.lock"
        lock = env_service.acquire_file_lock(lock_path, cfg.file_edit_lock_timeout_sec)
    except LockConflictError as exc:
        return _lock_error_response(exc)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse({
        "ok": True,
        "token": lock.token,
        "acquired_ts": lock.acquired_ts,
    })


@api_router.post(
    "/{name}/services/gateway/topology-json/unlock",
    dependencies=[require_permission("environment.config.update")],
)
async def release_gateway_topology_json_lock(
    request: Request, name: str, body: _UnlockBody
) -> JSONResponse:
    """Release the lock on the gateway device topology JSON file.

    Returns:
        JSONResponse with ok=True if the lock was released.

    Raises:
        HTTPException: If the environment is not found or topology is not configured.

    """
    cfg = _get_config(request)
    try:
        path = backend_service.resolve_topology_path(name, cfg)
        lock_path = path.parent / f"{path.name}.lock"
        env_service.release_file_lock(
            lock_path, body.token, cfg.file_edit_lock_timeout_sec
        )
    except LockNotHeldError as exc:
        return _lock_error_response(exc)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse({"ok": True})


@api_router.post(
    "/{name}/services/gateway/topology-json/save",
    dependencies=[require_permission("environment.config.update")],
)
async def save_gateway_topology_json(
    request: Request, name: str, body: _SaveBody
) -> JSONResponse:
    """Save the gateway device topology JSON file after validating the lock token.

    Returns:
        JSONResponse with ok=True on success.

    Raises:
        HTTPException: If the environment is not found or topology is not configured.

    """
    cfg = _get_config(request)
    try:
        path = backend_service.resolve_topology_path(name, cfg)
        lock_path = path.parent / f"{path.name}.lock"
        env_service.save_file(
            path,
            lock_path,
            body.content,
            body.token,
            cfg.file_edit_lock_timeout_sec,
        )
    except (LockExpiredError, LockTokenMismatchError) as exc:
        return _lock_error_response(exc)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse({"ok": True})


@api_router.get(
    "/{name}/services/gateway/topology-json/download",
    dependencies=[require_permission("environment.config.get")],
)
async def gateway_topology_json_download(request: Request, name: str) -> FileResponse:
    """Download the gateway device topology JSON file.

    Returns:
        FileResponse with the topology JSON content.

    Raises:
        HTTPException: If the environment/topology is not found/configured, or
            the topology file itself is missing.

    """
    cfg = _get_config(request)
    try:
        path = backend_service.resolve_topology_path(name, cfg)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Topology JSON file not found.")
    return FileResponse(path=path, filename=path.name, media_type="application/json")


@api_router.get(
    "/{name}/services/{service}/config/{which}/release-diff",
    dependencies=[require_permission("environment.config.get")],
)
async def service_config_release_diff(
    request: Request, name: str, service: str, which: str
) -> JSONResponse:
    """Return the installed release config content for diffing against the managed one.

    Returns:
        JSONResponse with installed_content and installed_path (either str or null).

    Raises:
        HTTPException: If the environment or config type is not found/recognized.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        filename = backend_service.config_which_to_filename(which)
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        installed_path = await backend_service.resolve_installed_config_path_via_info(
            cfg, name, service, filename, resolved
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    installed_content = None
    if installed_path is not None and installed_path.exists():
        installed_content = installed_path.read_text(encoding="utf-8")
    return JSONResponse({
        "installed_content": installed_content,
        "installed_path": str(installed_path) if installed_path else None,
    })


@api_router.get(
    "/{name}/services/gateway/topology-json/release-diff",
    dependencies=[require_permission("environment.config.get")],
)
async def gateway_topology_json_release_diff(
    request: Request, name: str
) -> JSONResponse:
    """Return the installed release topology JSON content for diffing.

    Returns:
        JSONResponse with installed_content and installed_path (either str or null).

    Raises:
        HTTPException: If the environment is not found or topology is not configured.

    """
    cfg = _get_config(request)
    try:
        env = env_service.get_environment_or_404(name, cfg)
        current_path = backend_service.resolve_topology_path(name, cfg)
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        installed_path = await backend_service.resolve_installed_config_path_via_info(
            cfg, name, "gateway", current_path.name, resolved
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    installed_content = None
    if installed_path is not None and installed_path.exists():
        installed_content = installed_path.read_text(encoding="utf-8")
    return JSONResponse({
        "installed_content": installed_content,
        "installed_path": str(installed_path) if installed_path else None,
    })
