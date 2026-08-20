"""Cloud-local-specific business logic."""

from __future__ import annotations

from typing import TYPE_CHECKING

from oqtopus_manager.services.environment import (
    get_environment_or_404,
    raise_for_command_result,
)
from oqtopus_manager.services.exceptions import InvalidArgumentError
from oqtopus_manager.util.cli import run_oqtopus_subcommand_output
from oqtopus_manager.util.parse import parse_info, parse_status, parse_versions_detailed

if TYPE_CHECKING:
    import pathlib

    from oqtopus_manager.config import AppConfig
    from oqtopus_manager.models.environment import Environment
    from oqtopus_manager.util.cli import CommandResult
    from oqtopus_manager.util.parse import EnvironmentData, StatusData, VersionsData

_SUBCOMMAND = "cloud-local"
_VALID_SERVICES = frozenset({
    "all",
    "db",
    "worker",
    "user_signup",
    "admin",
    "provider",
    "user",
})
# Single source of truth for the component list; this was previously
# duplicated across this module, routers/cloud_local/detail.py, and the
# detail-page template. Note "admin" is a component name here and also a
# service name in _VALID_SERVICES above -- distinct namespaces that happen
# to share a string.
COMPONENTS: tuple[str, ...] = ("cloud", "frontend", "admin")
_VALID_COMPONENTS = frozenset(COMPONENTS)
_SERVICE_CMDS = frozenset({"start", "stop", "restart"})
_COMPONENT_CMDS = frozenset({"versions", "install", "update", "uninstall"})


def build_list_context(environments: list[Environment], cfg: AppConfig) -> dict:
    """Build template context for the cloud-local list page.

    Deliberately excludes any per-environment CLI-derived data (status,
    all_installed): the list must render immediately from
    ``environments.yaml`` alone, without spawning a subprocess per
    environment. The client fetches
    ``GET /api/cloud-local?include_status=true`` after load to fill in the
    rest.

    Returns:
        Dict with env_items and metadata for the list template.

    """
    return {
        "env_items": [{"env": env} for env in environments],
        "base_path": cfg.default_environment_base_path,
        "url_prefix": "/cloud-local",
        "api_url_prefix": "/api/cloud-local",
        "page_title": "Cloud Local",
        "page_description": "Manage your OQTOPUS cloud-local environments.",
        "has_device_status": False,
    }


def get_log_file(env_root: pathlib.Path, service: str) -> pathlib.Path:
    """Return the log file path for a cloud-local service.

    Returns:
        Path to logs/<service>/service.log under env_root.

    """
    return env_root / "logs" / service / "service.log"


def validate_component(component: str, *, allow_all: bool = False) -> None:
    """Validate a component name against the known cloud-local components.

    Raises:
        InvalidArgumentError: If the component is not recognized.

    """
    valid = _VALID_COMPONENTS | {"all"} if allow_all else _VALID_COMPONENTS
    if component not in valid:
        msg = f"Invalid component '{component}'"
        raise InvalidArgumentError(msg)


def build_service_args(
    cmd: str,
    service: str,
    foreground: bool,  # noqa: FBT001
) -> list[str]:
    """Build argv for a service start/stop/restart command.

    Returns:
        List of string arguments to pass to the oqtopus cloud-local CLI.

    Raises:
        InvalidArgumentError: If the service is not recognized.

    """
    if service not in _VALID_SERVICES:
        msg = f"Invalid service '{service}'"
        raise InvalidArgumentError(msg)
    args = [cmd, service]
    if cmd == "start" and foreground:
        args.append("--foreground")
    return args


def build_component_args(cmd: str, component: str, version: str) -> list[str]:
    """Build argv for a component versions/install/update/uninstall command.

    Returns:
        List of string arguments to pass to the oqtopus cloud-local CLI.

    Raises:
        InvalidArgumentError: If the component is invalid, or version is
            missing for uninstall.

    """
    if cmd == "versions":
        validate_component(component)
        return ["versions", component]
    if cmd == "install":
        validate_component(component, allow_all=True)
        args = ["install", component]
        if component != "all" and version:
            args.append(version)
        return args
    if cmd == "update":
        validate_component(component)
        return ["update", component]
    validate_component(component)
    if not version:
        msg = "version is required for uninstall"
        raise InvalidArgumentError(msg)
    return ["uninstall", component, version]


def build_stream_args(
    cmd: str,
    service: str,
    component: str,
    version: str,
    foreground: bool,  # noqa: FBT001
) -> list[str]:
    """Translate validated query params into oqtopus cloud-local argv.

    ``status``/``info`` are deliberately not handled here: the UI reads
    them from the JSON endpoints (GET .../status, .../{name}) instead of
    this stream dispatcher. ``versions`` stays -- it still backs the
    console's raw-output display alongside the JSON endpoint.

    Returns:
        List of string arguments to pass to the oqtopus cloud-local CLI.

    Raises:
        InvalidArgumentError: If an invalid service, component, or command
            is provided.

    """
    if cmd in _SERVICE_CMDS:
        return build_service_args(cmd, service, foreground)
    if cmd in _COMPONENT_CMDS:
        return build_component_args(cmd, component, version)
    msg = f"Unknown command '{cmd}'"
    raise InvalidArgumentError(msg)


async def _run(
    cfg: AppConfig, name: str, args: list[str]
) -> tuple[Environment, CommandResult]:
    """Resolve *name* and run ``oqtopus cloud-local <args>``, raising on failure.

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
    """Run ``oqtopus cloud-local status`` and parse it into StatusData.

    Returns:
        StatusData for the ``GET /api/cloud-local/{name}/status`` response.

    """
    _, result = await _run(cfg, name, ["status"])
    return parse_status(result, template="cloud-local", environment_name=name)


async def get_info(cfg: AppConfig, name: str) -> EnvironmentData:
    """Run ``oqtopus cloud-local info`` and parse it into EnvironmentData.

    Returns:
        EnvironmentData for the ``GET /api/cloud-local/{name}`` response.

    """
    _, result = await _run(cfg, name, ["info"])
    return parse_info(
        result,
        template="cloud-local",
        components=COMPONENTS,
        strip_prefix="cloud_local_",
    )


async def get_component_versions_detailed(
    cfg: AppConfig, name: str, component: str
) -> VersionsData:
    """Run ``oqtopus cloud-local versions <component>`` and parse it into VersionsData.

    Returns:
        VersionsData for the components/{component}/versions response.

    Raises:
        InvalidArgumentError: If the component is not recognized.

    """
    if component not in _VALID_COMPONENTS:
        msg = f"Invalid component '{component}'"
        raise InvalidArgumentError(msg)
    _, result = await _run(cfg, name, ["versions", component])
    return parse_versions_detailed(result, template="cloud-local", component=component)
