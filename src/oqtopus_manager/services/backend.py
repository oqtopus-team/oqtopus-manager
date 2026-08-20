"""Backend-specific business logic."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import yaml

from oqtopus_manager.services.environment import (
    check_lock,
    get_environment_or_404,
    raise_for_command_result,
)
from oqtopus_manager.services.exceptions import (
    InvalidArgumentError,
    TopologyNotConfiguredError,
)
from oqtopus_manager.util.cli import run_oqtopus_subcommand_output
from oqtopus_manager.util.parse import (
    parse_device_status,
    parse_info,
    parse_status,
    parse_versions_detailed,
)

if TYPE_CHECKING:
    from oqtopus_manager.config import AppConfig
    from oqtopus_manager.models.environment import Environment
    from oqtopus_manager.util.cli import CommandResult
    from oqtopus_manager.util.parse import (
        DeviceStatusData,
        EnvironmentData,
        StatusData,
        VersionsData,
    )

_SUBCOMMAND = "backend"
_VALID_SERVICES = frozenset({
    "all",
    "core",
    "sse_engine",
    "mitigator",
    "estimator",
    "combiner",
    "tranqu",
    "gateway",
})
# Single source of truth for the component list; this was previously
# duplicated across this module, routers/backend/detail.py, and the
# detail-page template.
COMPONENTS: tuple[str, ...] = ("engine", "tranqu", "gateway")
_VALID_COMPONENTS = frozenset(COMPONENTS)
_VALID_STATUSES = frozenset({"active", "inactive", "maintenance"})


def build_list_context(environments: list[Environment], cfg: AppConfig) -> dict:
    """Build template context for the backend list page.

    Deliberately excludes any per-environment CLI-derived data (status,
    all_installed): the list must render immediately from
    ``environments.yaml`` alone, without spawning a subprocess per
    environment. The client fetches
    ``GET /api/backend?include_status=true`` after load to fill in the rest.

    Returns:
        Dict with env_items and base_path for the list template.

    """
    return {
        "env_items": [{"env": env} for env in environments],
        "base_path": cfg.default_environment_base_path,
        "url_prefix": "/backend",
        "api_url_prefix": "/api/backend",
        "page_title": "Backend",
        "page_description": "Manage your OQTOPUS backend environments.",
        "has_device_status": True,
    }


def build_stream_args(  # noqa: C901, PLR0911, PLR0912, PLR0913, PLR0917
    cmd: str,
    service: str,
    component: str,
    version: str,
    foreground: bool,  # noqa: FBT001
    status: str,
    skip_sse_build: bool,  # noqa: FBT001
) -> list[str]:
    """Translate validated query params into oqtopus backend argv.

    ``status``/``info``/``device-status-show`` are deliberately not handled
    here: the UI reads them from the JSON endpoints
    (GET .../status, .../device-status, .../{name}) instead of this
    stream dispatcher. ``versions`` stays -- it still backs the console's
    raw-output display alongside the JSON versions endpoint.

    Returns:
        List of string arguments to pass to the oqtopus backend CLI.

    Raises:
        InvalidArgumentError: If an invalid service, component, status, or
            command is provided.

    """
    if cmd in {"start", "stop", "restart"}:
        if service not in _VALID_SERVICES:
            msg = f"Invalid service '{service}'"
            raise InvalidArgumentError(msg)
        args = [cmd, service]
        if cmd == "start" and foreground:
            args.append("--foreground")
        return args
    if cmd == "versions":
        if component not in _VALID_COMPONENTS:
            msg = f"Invalid component '{component}'"
            raise InvalidArgumentError(msg)
        return ["versions", component]
    if cmd == "install":
        comp = component if component in _VALID_COMPONENTS else None
        if comp is None and component != "all":
            msg = f"Invalid component '{component}'"
            raise InvalidArgumentError(msg)
        args = ["install", component]
        if component != "all" and version:
            args.append(version)
        if skip_sse_build:
            args.append("--skip-sse-build")
        return args
    if cmd == "update":
        if component not in _VALID_COMPONENTS:
            msg = f"Invalid component '{component}'"
            raise InvalidArgumentError(msg)
        return ["update", component]
    if cmd == "uninstall":
        if component not in _VALID_COMPONENTS:
            msg = f"Invalid component '{component}'"
            raise InvalidArgumentError(msg)
        if not version:
            msg = "version is required for uninstall"
            raise InvalidArgumentError(msg)
        return ["uninstall", component, version]
    if cmd == "build":
        return ["build", "sse-runtime"]
    if cmd == "device-status-set":
        if status not in _VALID_STATUSES:
            msg = f"Invalid status '{status}'"
            raise InvalidArgumentError(msg)
        return ["device-status", status]
    msg = f"Unknown command '{cmd}'"
    raise InvalidArgumentError(msg)


async def _run(
    cfg: AppConfig, name: str, args: list[str]
) -> tuple[Environment, CommandResult]:
    """Resolve *name* and run ``oqtopus backend <args>``, raising on failure.

    Returns:
        The (Environment, CommandResult) pair for the caller to parse.

    """
    env = get_environment_or_404(name, cfg)
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    result = await run_oqtopus_subcommand_output(
        _SUBCOMMAND, args, cwd, cfg.oqtopus_cli_timeout_sec
    )
    raise_for_command_result(result)
    return env, result


async def get_status(cfg: AppConfig, name: str) -> StatusData:
    """Run ``oqtopus backend status`` and parse it into StatusData.

    Returns:
        StatusData for the ``GET /api/backend/{name}/status`` response.

    """
    _, result = await _run(cfg, name, ["status"])
    return parse_status(result, template="backend", environment_name=name)


async def get_device_status(cfg: AppConfig, name: str) -> DeviceStatusData:
    """Run ``oqtopus backend device-status show`` and parse it.

    Returns:
        DeviceStatusData for the ``GET /api/backend/{name}/device-status`` response.

    """
    _, result = await _run(cfg, name, ["device-status", "show"])
    return parse_device_status(result, template="backend", environment_name=name)


async def get_info(cfg: AppConfig, name: str) -> EnvironmentData:
    """Run ``oqtopus backend info`` and parse it into EnvironmentData.

    Returns:
        EnvironmentData for the ``GET /api/backend/{name}`` response.

    """
    _, result = await _run(cfg, name, ["info"])
    return parse_info(result, template="backend", components=COMPONENTS)


async def get_component_versions_detailed(
    cfg: AppConfig, name: str, component: str
) -> VersionsData:
    """Run ``oqtopus backend versions <component>`` and parse it into VersionsData.

    Returns:
        VersionsData for the components/{component}/versions response.

    Raises:
        InvalidArgumentError: If the component is not recognized.

    """
    if component not in _VALID_COMPONENTS:
        msg = f"Invalid component '{component}'"
        raise InvalidArgumentError(msg)
    _, result = await _run(cfg, name, ["versions", component])
    return parse_versions_detailed(result, template="backend", component=component)


def _info_to_meta(info: EnvironmentData) -> dict[str, str]:
    """Reshape EnvironmentData into the dict resolve_installed_config_path expects.

    Bridges the ``info()`` call to the pre-existing path-resolution logic,
    which still keys off the old ``.metadata`` shape, without duplicating
    that logic.

    Returns:
        Dict with ``install_root`` and ``{component}_version`` keys.

    """
    meta: dict[str, str] = {}
    if info.install_root:
        meta["install_root"] = info.install_root
    for component in info.components:
        if component.version:
            meta[f"{component.name}_version"] = component.version
    return meta


async def resolve_installed_config_path_via_info(
    cfg: AppConfig, name: str, service: str, filename: str, env_root: pathlib.Path
) -> pathlib.Path | None:
    """Resolve an installed config path using ``info`` instead of ``.metadata``.

    Returns:
        Resolved Path, or None when the service is unknown or install_root
        is absent for a release version.

    """
    info = await get_info(cfg, name)
    meta = _info_to_meta(info)
    return resolve_installed_config_path(service, filename, meta, env_root)


def _extract_path_from_yaml(
    yaml_file: pathlib.Path, keys: list[str], env_root: pathlib.Path
) -> pathlib.Path | None:
    """Load a YAML file and follow a chain of keys to a path value.

    Returns:
        The resolved Path, or None if any key in the chain is missing.

    """
    data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    value: object = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if not value:
        return None
    path = pathlib.Path(str(value))
    # Resolve relative paths against env_root rather than the process cwd
    return path if path.is_absolute() else env_root / path


def read_path_from_yaml(
    yaml_file: pathlib.Path, keys: list[str], env_root: pathlib.Path
) -> pathlib.Path | None:
    """Read a file path from a YAML file by following a chain of keys.

    Returns:
        The resolved Path, or None if not found or on any parsing error.

    """
    if not yaml_file.exists():
        return None
    try:
        return _extract_path_from_yaml(yaml_file, keys, env_root)
    except KeyError, TypeError, AttributeError:
        return None


def get_log_file(env_root: pathlib.Path, service: str) -> pathlib.Path | None:
    """Return the log file path from the service logging.yaml, or None.

    Returns:
        The Path to the log file, or None if it cannot be determined.

    """
    return read_path_from_yaml(
        env_root / "config" / service / "logging.yaml",
        ["handlers", "file", "filename"],
        env_root,
    )


def get_topology_json_path(env_root: pathlib.Path) -> pathlib.Path | None:
    """Return device_topology_json_path from gateway config.yaml, or None.

    Returns:
        The resolved Path to the topology JSON file, or None if not configured.

    """
    return read_path_from_yaml(
        env_root / "config" / "gateway" / "config.yaml",
        ["device_topology_json_path"],
        env_root,
    )


def load_topology_context(service: str, resolved: pathlib.Path, timeout: int) -> dict:
    """Build topology JSON template context for the service config page.

    Returns:
        Dict with topology_json_path, topology_content, and lock state keys.

    """
    empty: dict = {
        "topology_json_path": None,
        "topology_content": None,
        "topology_is_locked": False,
        "topology_locked_since": None,
        "topology_locked_since_ts": None,
    }
    if service != "gateway":
        return empty
    path = get_topology_json_path(resolved)
    if path is None:
        return empty
    content = path.read_text(encoding="utf-8") if path.exists() else None
    lock_path = path.parent / f"{path.name}.lock"
    state = check_lock(lock_path, timeout)
    return {
        "topology_json_path": path,
        "topology_content": content,
        "topology_is_locked": state.is_locked,
        "topology_locked_since": state.locked_since,
        "topology_locked_since_ts": state.locked_since_ts,
    }


def resolve_topology_path(name: str, cfg: AppConfig) -> pathlib.Path:
    """Look up the topology JSON path for a named environment.

    Returns:
        The resolved topology JSON file Path.

    Raises:
        TopologyNotConfiguredError: If the topology path is not configured.

    """
    env = get_environment_or_404(name, cfg)
    resolved = env.resolved_root_path(cfg.default_environment_base_path)
    path = get_topology_json_path(resolved)
    if path is None:
        msg = "device_topology_json_path not configured in gateway config."
        raise TopologyNotConfiguredError(msg)
    return path


_ENGINE_SERVICES = frozenset({
    "core",
    "sse_engine",
    "combiner",
    "estimator",
    "mitigator",
})

# sse_engine config files are prefixed in the release package
_SSE_ENGINE_FILENAME: dict[str, str] = {
    "config.yaml": "sse_engine_config.yaml",
    "logging.yaml": "sse_engine_logging.yaml",
}

# sse_engine ships inside core's source tree in the release package
_ENGINE_RELEASE_SUBDIR: dict[str, str] = {
    "core": "core",
    "sse_engine": "core",
    "combiner": "combiner",
    "estimator": "estimator",
    "mitigator": "mitigator",
}

# Maps non-engine service names to (version_key, directory_prefix)
_COMPONENT_MAP: dict[str, tuple[str, str]] = {
    "tranqu": ("tranqu_version", "tranqu"),
    "gateway": ("gateway_version", "gateway"),
}

# gateway config.yaml uses a different filename in the release package
_GATEWAY_FILENAME: dict[str, str] = {
    "config.yaml": "config.yaml.qulacs",
}

# standard per-service config files for gateway; anything else is a topology file
_GATEWAY_KNOWN_CONFIGS = frozenset({"config.yaml", "logging.yaml"})


def resolve_installed_config_path(
    service: str,
    filename: str,
    meta: dict[str, str],
    env_root: pathlib.Path,
) -> pathlib.Path | None:
    """Resolve the path to the installed (release or branch) config file.

    Returns:
        Resolved Path, or None when the service is unknown or install_root
        is absent for a release version.

    """
    install_root = meta.get("install_root")
    release_parts: tuple[str, ...]

    if service in _ENGINE_SERVICES:
        version = meta.get("engine_version", "")
        subdir = _ENGINE_RELEASE_SUBDIR[service]
        branch_path = env_root / "engine" / subdir / "config" / filename
        # sse_engine uses a prefixed filename in the release package
        if service == "sse_engine":
            release_filename = _SSE_ENGINE_FILENAME.get(filename, filename)
        else:
            release_filename = filename
        # Engine release layout: {install_root}/engine-{version}/{subdir}/config/
        release_parts = (f"engine-{version}", subdir, "config", release_filename)
    elif service in _COMPONENT_MAP:
        version_key, component = _COMPONENT_MAP[service]
        version = meta.get(version_key, "")
        branch_path = env_root / component / "config" / filename
        if component == "gateway":
            if filename in _GATEWAY_KNOWN_CONFIGS:
                release_filename = _GATEWAY_FILENAME.get(filename, filename)
                release_parts = (f"{component}-{version}", "config", release_filename)
            else:
                # topology JSON files live in config/example/ in the release package
                release_parts = (
                    f"{component}-{version}",
                    "config",
                    "example",
                    filename,
                )
        else:
            release_parts = (f"{component}-{version}", "config", filename)
    else:
        return None

    if version.startswith("branch:"):
        return branch_path
    if not install_root:
        return None
    return pathlib.Path(install_root).joinpath(*release_parts)


def components_installed(install_root: str) -> bool:
    """Return True if at least one component directory exists under install_root.

    Returns:
        True if at least one component directory is present.

    """
    root = pathlib.Path(install_root)
    return any((root / comp).is_dir() for comp in ("engine", "tranqu", "gateway"))


def config_which_to_filename(which: str) -> str:
    """Map a "config"/"logging" query param to its filename.

    Returns:
        "config.yaml" or "logging.yaml".

    Raises:
        InvalidArgumentError: If ``which`` is neither "config" nor "logging".

    """
    if which not in {"config", "logging"}:
        msg = f"Unknown config type: {which!r}."
        raise InvalidArgumentError(msg)
    return f"{which}.yaml"
