"""Routes for backend service config editor and gateway topology JSON."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request

if TYPE_CHECKING:
    import pathlib
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from oqtopus_auth.fastapi import require_permission

from oqtopus_manager.routers._file_edit import (
    _acquire_file_lock,
    _check_lock,
    _force_unlock_file,
    _release_file_lock,
    _save_file,
    _SaveBody,
    _UnlockBody,
)
from oqtopus_manager.routers._utils import (
    _get_config,
    _get_environment_or_404,
    _get_templates,
)
from oqtopus_manager.routers.backend._utils import (
    _config_which_to_filename,
    _load_topology_context,
    _read_metadata,
    _resolve_installed_config_path,
    _resolve_topology_path,
)

router = APIRouter(prefix="/backend", tags=["backend"])


@router.get(
    "/{name}/services/{service}/config",
    response_class=HTMLResponse,
    dependencies=[require_permission("environment.config.get")],
)
async def service_config(request: Request, name: str, service: str) -> HTMLResponse:
    """Render the service config editor page.

    Returns:
        HTMLResponse with the service config editor.

    """
    cfg = _get_config(request)
    env = _get_environment_or_404(name, cfg)
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    config_dir = resolved / "config" / service

    def _read(path: pathlib.Path) -> str | None:
        return path.read_text(encoding="utf-8") if path.exists() else None

    config_locked, _, config_locked_since, config_locked_since_ts = _check_lock(
        config_dir / "config.yaml.lock", cfg.file_edit_lock_timeout_sec
    )
    logging_locked, _, logging_locked_since, logging_locked_since_ts = _check_lock(
        config_dir / "logging.yaml.lock", cfg.file_edit_lock_timeout_sec
    )

    return _get_templates(request).TemplateResponse(
        request,
        "environments/service_config.html",
        {
            "env": env,
            "service": service,
            "config_dir": config_dir,
            "config_content": _read(config_dir / "config.yaml"),
            "logging_content": _read(config_dir / "logging.yaml"),
            "config_is_locked": config_locked,
            "config_locked_since": config_locked_since,
            "config_locked_since_ts": config_locked_since_ts,
            "logging_is_locked": logging_locked,
            "logging_locked_since": logging_locked_since,
            "logging_locked_since_ts": logging_locked_since_ts,
            **_load_topology_context(service, resolved, cfg.file_edit_lock_timeout_sec),
            "lock_timeout_sec": cfg.file_edit_lock_timeout_sec,
        },
    )


@router.post(
    "/{name}/services/{service}/config/{which}/force-unlock",
    dependencies=[require_permission("environment.config.update")],
)
async def force_unlock_service_config(
    request: Request, name: str, service: str, which: str
) -> JSONResponse:
    """Force-unlock a service config file.

    Returns:
        JSONResponse with ok=True on success.

    """
    cfg = _get_config(request)
    env = _get_environment_or_404(name, cfg)
    filename = _config_which_to_filename(which)
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    return _force_unlock_file(resolved / "config" / service / f"{filename}.lock")


@router.post(
    "/{name}/services/{service}/config/{which}/lock",
    dependencies=[require_permission("environment.config.update")],
)
async def acquire_service_config_lock(
    request: Request, name: str, service: str, which: str
) -> JSONResponse:
    """Acquire a lock on a service config file.

    Returns:
        JSONResponse with ok=True and token on success, or conflict info.

    """
    cfg = _get_config(request)
    env = _get_environment_or_404(name, cfg)
    filename = _config_which_to_filename(which)
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    lock_path = resolved / "config" / service / f"{filename}.lock"
    return _acquire_file_lock(lock_path, cfg.file_edit_lock_timeout_sec)


@router.post(
    "/{name}/services/{service}/config/{which}/unlock",
    dependencies=[require_permission("environment.config.update")],
)
async def release_service_config_lock(
    request: Request, name: str, service: str, which: str, body: _UnlockBody
) -> JSONResponse:
    """Release a lock on a service config file.

    Returns:
        JSONResponse with ok=True if the lock was released.

    """
    cfg = _get_config(request)
    env = _get_environment_or_404(name, cfg)
    filename = _config_which_to_filename(which)
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    lock_path = resolved / "config" / service / f"{filename}.lock"
    return _release_file_lock(lock_path, body.token, cfg.file_edit_lock_timeout_sec)


@router.post(
    "/{name}/services/{service}/config/{which}/save",
    dependencies=[require_permission("environment.config.update")],
)
async def save_service_config(
    request: Request, name: str, service: str, which: str, body: _SaveBody
) -> JSONResponse:
    """Save a service config file after validating the lock token.

    Returns:
        JSONResponse with ok=True on success.

    """
    cfg = _get_config(request)
    env = _get_environment_or_404(name, cfg)
    filename = _config_which_to_filename(which)
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    config_path = resolved / "config" / service / filename
    lock_path = resolved / "config" / service / f"{filename}.lock"
    return _save_file(
        config_path, lock_path, body.content, body.token, cfg.file_edit_lock_timeout_sec
    )


@router.post(
    "/{name}/services/gateway/topology-json/force-unlock",
    dependencies=[require_permission("environment.config.update")],
)
async def force_unlock_gateway_topology_json(
    request: Request, name: str
) -> JSONResponse:
    """Force-unlock the gateway device topology JSON file.

    Returns:
        JSONResponse with ok=True on success.

    """
    cfg = _get_config(request)
    path = _resolve_topology_path(name, cfg)
    return _force_unlock_file(path.parent / f"{path.name}.lock")


@router.post(
    "/{name}/services/gateway/topology-json/lock",
    dependencies=[require_permission("environment.config.update")],
)
async def acquire_gateway_topology_json_lock(
    request: Request, name: str
) -> JSONResponse:
    """Acquire a lock on the gateway device topology JSON file.

    Returns:
        JSONResponse with ok=True and token on success, or conflict info.

    """
    cfg = _get_config(request)
    path = _resolve_topology_path(name, cfg)
    lock_path = path.parent / f"{path.name}.lock"
    return _acquire_file_lock(lock_path, cfg.file_edit_lock_timeout_sec)


@router.post(
    "/{name}/services/gateway/topology-json/unlock",
    dependencies=[require_permission("environment.config.update")],
)
async def release_gateway_topology_json_lock(
    request: Request, name: str, body: _UnlockBody
) -> JSONResponse:
    """Release the lock on the gateway device topology JSON file.

    Returns:
        JSONResponse with ok=True if the lock was released.

    """
    cfg = _get_config(request)
    path = _resolve_topology_path(name, cfg)
    lock_path = path.parent / f"{path.name}.lock"
    return _release_file_lock(lock_path, body.token, cfg.file_edit_lock_timeout_sec)


@router.post(
    "/{name}/services/gateway/topology-json/save",
    dependencies=[require_permission("environment.config.update")],
)
async def save_gateway_topology_json(
    request: Request, name: str, body: _SaveBody
) -> JSONResponse:
    """Save the gateway device topology JSON file after validating the lock token.

    Returns:
        JSONResponse with ok=True on success.

    """
    cfg = _get_config(request)
    path = _resolve_topology_path(name, cfg)
    lock_path = path.parent / f"{path.name}.lock"
    return _save_file(
        path, lock_path, body.content, body.token, cfg.file_edit_lock_timeout_sec
    )


@router.get(
    "/{name}/services/gateway/topology-json/download",
    dependencies=[require_permission("environment.config.get")],
)
async def gateway_topology_json_download(request: Request, name: str) -> FileResponse:
    """Download the gateway device topology JSON file.

    Returns:
        FileResponse with the topology JSON content.

    Raises:
        HTTPException: If the topology file is not found.

    """
    cfg = _get_config(request)
    path = _resolve_topology_path(name, cfg)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Topology JSON file not found.")
    return FileResponse(path=path, filename=path.name, media_type="application/json")


@router.get(
    "/{name}/services/{service}/config/{which}/release-diff",
    dependencies=[require_permission("environment.config.get")],
)
async def service_config_release_diff(
    request: Request, name: str, service: str, which: str
) -> JSONResponse:
    """Return the installed release config content for diffing against the managed one.

    Returns:
        JSONResponse with installed_content and installed_path (either str or null).

    """
    cfg = _get_config(request)
    env = _get_environment_or_404(name, cfg)
    filename = _config_which_to_filename(which)
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    meta = _read_metadata(resolved)
    installed_path = _resolve_installed_config_path(service, filename, meta, resolved)
    installed_content = None
    if installed_path is not None and installed_path.exists():
        installed_content = installed_path.read_text(encoding="utf-8")
    return JSONResponse({
        "installed_content": installed_content,
        "installed_path": str(installed_path) if installed_path else None,
    })


@router.get(
    "/{name}/services/gateway/topology-json/release-diff",
    dependencies=[require_permission("environment.config.get")],
)
async def gateway_topology_json_release_diff(
    request: Request, name: str
) -> JSONResponse:
    """Return the installed release topology JSON content for diffing.

    Returns:
        JSONResponse with installed_content and installed_path (either str or null).

    """
    cfg = _get_config(request)
    env = _get_environment_or_404(name, cfg)
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    current_path = _resolve_topology_path(name, cfg)
    meta = _read_metadata(resolved)
    installed_path = _resolve_installed_config_path(
        "gateway", current_path.name, meta, resolved
    )
    installed_content = None
    if installed_path is not None and installed_path.exists():
        installed_content = installed_path.read_text(encoding="utf-8")
    return JSONResponse({
        "installed_content": installed_content,
        "installed_path": str(installed_path) if installed_path else None,
    })
