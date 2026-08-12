"""Cloud-local-specific shared helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from oqtopus_manager.routers._utils import _read_metadata as _read_metadata_generic

if TYPE_CHECKING:
    import pathlib

    from oqtopus_manager.config import AppConfig
    from oqtopus_manager.models.environment import Environment

_VERSION_KEYS = ["cloud_version", "frontend_version", "admin_version"]


def _read_metadata(env_root: pathlib.Path) -> dict[str, str]:
    """Parse <env_root>/.metadata (key=value lines) into a dict.

    Strips the ``cloud_local_`` prefix so keys match
    cloud_version/frontend_version/admin_version.

    Returns:
        Dict mapping key strings to value strings.

    """
    return _read_metadata_generic(env_root, strip_prefix="cloud_local_")


def _build_list_context(environments: list[Environment], cfg: AppConfig) -> dict:
    """Build template context for the cloud-local list page.

    Returns:
        Dict with env_items and metadata for the list template.

    """
    env_items = []
    for env in environments:
        resolved = env.resolved_root_path(cfg.default_environment_base_path)
        meta = _read_metadata(resolved)
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


def _get_log_file(env_root: pathlib.Path, service: str) -> pathlib.Path:
    """Return the log file path for a cloud-local service.

    Returns:
        Path to logs/<service>/service.log under env_root.

    """
    return env_root / "logs" / service / "service.log"
