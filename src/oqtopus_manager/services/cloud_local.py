"""Cloud-local-specific business logic."""

from __future__ import annotations

from typing import TYPE_CHECKING

from oqtopus_manager.services.environment import get_environment_or_404
from oqtopus_manager.services.environment import read_metadata as read_metadata_generic
from oqtopus_manager.services.exceptions import CommandFailedError, InvalidArgumentError
from oqtopus_manager.util.cli import run_oqtopus_subcommand_output
from oqtopus_manager.util.parse import parse_versions

if TYPE_CHECKING:
    import pathlib

    from oqtopus_manager.config import AppConfig
    from oqtopus_manager.models.environment import Environment

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
_VALID_COMPONENTS = frozenset({"cloud", "frontend", "admin"})
_SERVICE_CMDS = frozenset({"start", "stop", "restart"})
_COMPONENT_CMDS = frozenset({"versions", "install", "update", "uninstall"})

VERSION_KEYS = ["cloud_version", "frontend_version", "admin_version"]


def read_metadata(env_root: pathlib.Path) -> dict[str, str]:
    """Parse <env_root>/.metadata (key=value lines) into a dict.

    Strips the ``cloud_local_`` prefix so keys match
    cloud_version/frontend_version/admin_version.

    Returns:
        Dict mapping key strings to value strings.

    """
    return read_metadata_generic(env_root, strip_prefix="cloud_local_")


def build_list_context(environments: list[Environment], cfg: AppConfig) -> dict:
    """Build template context for the cloud-local list page.

    Returns:
        Dict with env_items and metadata for the list template.

    """
    env_items = []
    for env in environments:
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        meta = read_metadata(resolved)
        all_installed = bool(
            meta.get("cloud_version")
            and meta.get("frontend_version")
            and meta.get("admin_version")
        )
        env_items.append({"env": env, "all_installed": all_installed})
    return {
        "env_items": env_items,
        "base_path": cfg.default_environment_base_path,
        "url_prefix": "/cloud-local",
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

    Returns:
        List of string arguments to pass to the oqtopus cloud-local CLI.

    Raises:
        InvalidArgumentError: If an invalid service, component, or command
            is provided.

    """
    if cmd in {"status", "info"}:
        return [cmd]
    if cmd in _SERVICE_CMDS:
        return build_service_args(cmd, service, foreground)
    if cmd in _COMPONENT_CMDS:
        return build_component_args(cmd, component, version)
    msg = f"Unknown command '{cmd}'"
    raise InvalidArgumentError(msg)


async def get_component_versions(
    cfg: AppConfig, name: str, component: str
) -> list[str]:
    """Run ``oqtopus cloud-local versions <component>`` and parse the version list.

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
        _SUBCOMMAND, ["versions", component], cwd
    )
    if not result.ok:
        msg = (
            result.stderr.strip()
            or f"oqtopus {_SUBCOMMAND} versions {component} failed"
        )
        raise CommandFailedError(msg)
    return parse_versions(result)
