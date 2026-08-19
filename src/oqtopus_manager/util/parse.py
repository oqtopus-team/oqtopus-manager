"""Parsers for ``oqtopus`` CLI output.

Kept in one place so a future change to the CLI's output format (e.g. a
Rust reimplementation) only needs updating here rather than at every call
site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oqtopus_manager.util.cli import CommandResult

_VERSION_RE = re.compile(r"branch:\S+|v\d+[\w.+-]*")


@dataclass(frozen=True)
class ServiceStatus:
    """A single service's reported state, e.g. from ``oqtopus backend status``."""

    name: str
    state: str

    @property
    def running(self) -> bool:
        """Whether this service is reported as running.

        Returns:
            True if the state string starts with "running" (case-insensitive).

        """
        return self.state.lower().startswith("running")


def parse_service_status(result: CommandResult) -> list[ServiceStatus]:
    """Parse ``name: state`` lines from an ``oqtopus <subcommand> status`` result.

    Returns:
        One ServiceStatus per recognized ``name: state`` line.

    """
    services = []
    for line in result.stdout.splitlines():
        name, sep, state = line.partition(":")
        if sep:
            services.append(ServiceStatus(name=name.strip(), state=state.strip()))
    return services


def parse_versions(result: CommandResult) -> list[str]:
    """Extract version/branch tokens from an ``oqtopus <subcommand> versions`` result.

    Returns:
        Matched version strings (e.g. ``v1.2.3``) and branch tokens (e.g.
        ``branch:main``), in output order.

    """
    return [
        m.group()
        for line in result.stdout.splitlines()
        if (m := _VERSION_RE.search(line))
    ]
