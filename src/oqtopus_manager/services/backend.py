"""Backend-specific business logic."""

from __future__ import annotations

import pathlib
import re
from typing import TYPE_CHECKING

import yaml

from oqtopus_manager.services.environment import (
    check_lock,
    get_environment_or_404,
    read_metadata,
)
from oqtopus_manager.services.exceptions import (
    CommandFailedError,
    InvalidArgumentError,
    TopologyNotConfiguredError,
)
from oqtopus_manager.util.cli import run_oqtopus_subcommand_output

if TYPE_CHECKING:
    from oqtopus_manager.config import AppConfig
    from oqtopus_manager.models.environment import Environment

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
_VALID_COMPONENTS = frozenset({"engine", "tranqu", "gateway"})
_VALID_STATUSES = frozenset({"active", "inactive", "maintenance"})


def build_list_context(environments: list[Environment], cfg: AppConfig) -> dict:
    """Build template context for the backend list page.

    Returns:
        Dict with env_items (list of dicts with env and all_installed) and base_path.

    """
    env_items = []
    for env in environments:
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        meta = read_metadata(resolved)
        # All three service versions must be present for the env to be fully installed
        all_installed = bool(
            meta.get("engine_version")
            and meta.get("tranqu_version")
            and meta.get("gateway_version")
        )
        env_items.append({"env": env, "all_installed": all_installed})
    return {
        "env_items": env_items,
        "base_path": cfg.default_environment_base_path,
        "url_prefix": "/backend",
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

    Returns:
        List of string arguments to pass to the oqtopus backend CLI.

    Raises:
        InvalidArgumentError: If an invalid service, component, status, or
            command is provided.

    """
    if cmd == "status":
        return ["status"]
    if cmd == "info":
        return ["info"]
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
    if cmd == "device-status-show":
        return ["device-status", "show"]
    if cmd == "device-status-set":
        if status not in _VALID_STATUSES:
            msg = f"Invalid status '{status}'"
            raise InvalidArgumentError(msg)
        return ["device-status", status]
    msg = f"Unknown command '{cmd}'"
    raise InvalidArgumentError(msg)


async def get_component_versions(
    cfg: AppConfig, name: str, component: str
) -> list[str]:
    """Run ``oqtopus backend versions <component>`` and parse the version list.

    Returns:
        Matched version strings and branch tokens, in output order.

    Raises:
        InvalidArgumentError: If the component is not recognized.
        CommandFailedError: If the CLI invocation fails.

    """
    if component not in _VALID_COMPONENTS:
        msg = f"Invalid component '{component}'"
        raise InvalidArgumentError(msg)

    env = get_environment_or_404(name, cfg)
    cwd = env.resolved_root_path(cfg.default_environment_base_path)
    result = await run_oqtopus_subcommand_output(
        "backend", ["versions", component], cwd
    )
    if not result.ok:
        msg = result.stderr.strip() or f"oqtopus backend versions {component} failed"
        raise CommandFailedError(msg)
    return [
        m.group()
        for line in result.stdout.splitlines()
        if (m := re.search(r"branch:\S+|v\d+[\w.+-]*", line))
    ]


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
