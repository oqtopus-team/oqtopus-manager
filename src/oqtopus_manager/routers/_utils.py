"""Shared helper functions for environment router modules."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from oqtopus_manager.util.cli import run_oqtopus_subcommand_output
from oqtopus_manager.util.parse import parse_service_status

if TYPE_CHECKING:
    import pathlib

    from fastapi.templating import Jinja2Templates

    from oqtopus_manager.config import AppConfig
    from oqtopus_manager.models.environment import Environment

logger = logging.getLogger(__name__)


def _get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def _get_config(request: Request) -> AppConfig:
    return request.app.state.config


def _get_environment_or_404(name: str, cfg: AppConfig) -> Environment:
    """Return the named environment, raising 404 if not found.

    Returns:
        The matching Environment.

    Raises:
        HTTPException: If the environment is not found.

    """
    env = next((e for e in cfg.load_environments() if e.name == name), None)
    if env is None:
        raise HTTPException(status_code=404, detail=f"Environment '{name}' not found.")
    return env


async def _has_running_services(subcommand: str, root_dir: pathlib.Path) -> bool:
    """Return True if ``oqtopus <subcommand> status`` reports any running service.

    Fails closed: if the status check itself cannot be completed, the state
    is unknown, so this reports "running" rather than "no services running"
    so callers (e.g. environment deletion) don't treat a broken status check
    as permission to proceed.

    Returns:
        True if at least one service line reports a running state, or if the
        status check failed.

    """
    result = await run_oqtopus_subcommand_output(subcommand, ["status"], root_dir)
    if not result.ok:
        logger.warning(
            "oqtopus %s status failed (returncode=%d): %s",
            subcommand,
            result.returncode,
            result.stderr.strip(),
        )
        return True
    return any(service.running for service in parse_service_status(result))


def _read_metadata(env_root: pathlib.Path, strip_prefix: str = "") -> dict[str, str]:
    """Parse <env_root>/.metadata (key=value lines) into a dict.

    ``strip_prefix`` is removed from each key, for templates whose metadata
    keys are namespaced (e.g. ``cloud_local_frontend_version``).

    Returns:
        Dict mapping key strings to value strings.

    """
    path = env_root / ".metadata"
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip().removeprefix(strip_prefix)] = value.strip()
    return result
