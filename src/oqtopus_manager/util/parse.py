"""Parsers for ``oqtopus`` CLI output.

Kept in one place so a future change to the CLI's output format (e.g. a
Rust reimplementation adding ``--json``) only needs updating here rather
than at every call site. The parsed models mirror the CLI JSON output
schema (oqtopus-cli-json-output-schema.md) so that when ``--json`` lands,
these functions can be swapped for ``json.loads`` without changing the
Manager's HTTP contract.

Known-value fields (``state``, ``kind``, ``device_status``) are kept as
plain ``str``/``str | None`` rather than ``Literal`` so an unrecognized
value from a newer CLI passes through unchanged instead of raising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from oqtopus_manager.util.cli import CommandResult

_TAG_RE = re.compile(r"v\d+")


@dataclass(frozen=True)
class ServiceStatus:
    """A single service's reported state, e.g. from ``oqtopus backend status``.

    Superseded by :class:`ServiceStatusItem`/:func:`parse_status` for the new
    JSON endpoints; kept for ``has_running_services``, whose fail-closed
    running-check does not need kind/pid/container detail.
    """

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


# ── Structured models ────────────────────────────────────────────────────────


class ServiceStatusItem(BaseModel):
    """One service's status, e.g. one row of ``oqtopus backend status``."""

    name: str
    kind: str  # "process" | "container"; unknown values pass through
    state: str  # "running" | "stopped"; unknown values pass through
    pid: int | None = None
    containers: list[str] | None = None


class StatusData(BaseModel):
    """Response body for ``GET /api/{template}/{name}/status``."""

    template: str
    environment_name: str
    services: list[ServiceStatusItem]


class DeviceStatusData(BaseModel):
    """Response body for ``GET /api/backend/{name}/device-status``."""

    template: str
    environment_name: str
    device_status: str | None  # "active" | "inactive" | "maintenance" | None


class ComponentInfo(BaseModel):
    """One component's installed version, as reported by ``info``."""

    name: str
    version: str | None
    kind: str | None  # "tag" | "branch" | "other"; None iff version is None


class EnvironmentData(BaseModel):
    """Response body for ``GET /api/{template}/{name}`` (CLI ``info``)."""

    template: str
    environment_name: str
    environment_root: str | None = None
    install_root: str | None = None
    created_at: str | None = None
    components: list[ComponentInfo]
    all_installed: bool
    extra: dict[str, str] = {}


class VersionEntry(BaseModel):
    """One available version, as reported by ``versions``."""

    version: str
    kind: str  # "tag" | "branch" | "other"
    installed: bool
    in_remote: bool


class VersionsData(BaseModel):
    """Response body for ``GET /api/{template}/{name}/components/{c}/versions``."""

    template: str
    component: str
    current: str | None
    versions: list[VersionEntry]


def _normalize_state(raw: str) -> str:
    """Map a free-form CLI state string to "running"/"stopped", or pass through.

    Returns:
        "running" or "stopped" for recognized prefixes; otherwise the
        lowercased, stripped input, since an unrecognized state should pass
        through rather than raise.

    """
    low = raw.strip().lower()
    if low.startswith("running"):
        return "running"
    if low.startswith("stopped"):
        return "stopped"
    return low


def _component_kind(version: str | None) -> str | None:
    """Classify a version string as a branch, tag, or other reference.

    Returns:
        None if version is None, else "branch", "tag", or "other".

    """
    if version is None:
        return None
    if version.startswith("branch:"):
        return "branch"
    if _TAG_RE.match(version):
        return "tag"
    return "other"


# Services reported as one or more container names rather than a PID.
# Not derivable from the text output alone (a "Stopped" line carries no
# kind information), so it has to be hardcoded here until the CLI reports
# `kind` directly.
_CONTAINER_SERVICE_NAMES = frozenset({"db"})

_PID_RE = re.compile(r"running\s*\(pid\s+(\d+)\)", re.IGNORECASE)
_CONTAINER_LIST_RE = re.compile(r"running\s*\(([^)]+)\)", re.IGNORECASE)


def parse_status(
    result: CommandResult, *, template: str, environment_name: str
) -> StatusData:
    """Parse ``oqtopus <subcommand> status`` into structured per-service rows.

    Line-based, not template-based: cloud-local's output mixes container
    rows (``db``) with process rows in a single command, so dispatch happens
    per line rather than per template.

    Returns:
        StatusData for the ``GET /api/{template}/{name}/status`` response.

    """
    services: list[ServiceStatusItem] = []
    for line in result.stdout.splitlines():
        name, sep, state_raw = line.partition(":")
        if not sep:
            continue
        name = name.strip()
        state_raw = state_raw.strip()

        pid_match = _PID_RE.search(state_raw)
        if pid_match:
            services.append(
                ServiceStatusItem(
                    name=name,
                    kind="process",
                    state="running",
                    pid=int(pid_match.group(1)),
                )
            )
            continue

        if name in _CONTAINER_SERVICE_NAMES:
            container_match = _CONTAINER_LIST_RE.search(state_raw)
            containers = (
                [c.strip() for c in container_match.group(1).split(",")]
                if container_match
                else []
            )
            services.append(
                ServiceStatusItem(
                    name=name,
                    kind="container",
                    state="running" if container_match else _normalize_state(state_raw),
                    containers=containers,
                )
            )
            continue

        services.append(
            ServiceStatusItem(
                name=name,
                kind="process",
                state=_normalize_state(state_raw),
            )
        )
    return StatusData(
        template=template, environment_name=environment_name, services=services
    )


def parse_device_status(
    result: CommandResult, *, template: str, environment_name: str
) -> DeviceStatusData:
    """Parse ``oqtopus backend device-status show`` output.

    Judged in maintenance -> inactive -> active order because "inactive"
    contains "active" as a substring; reordering misclassifies "inactive"
    as "active".

    Returns:
        DeviceStatusData for the device-status response.

    """
    low = result.stdout.lower()
    if "maintenance" in low:
        status: str | None = "maintenance"
    elif "inactive" in low:
        status = "inactive"
    elif "active" in low:
        status = "active"
    else:
        status = None
    return DeviceStatusData(
        template=template, environment_name=environment_name, device_status=status
    )


def parse_info(
    result: CommandResult,
    *,
    template: str,
    components: tuple[str, ...],
    strip_prefix: str = "",
) -> EnvironmentData:
    """Parse ``oqtopus <subcommand> info`` (``key=value`` lines) into EnvironmentData.

    ``components`` drives a left outer join: every known component is
    listed even if uninstalled (``version=None``), and any
    ``*_version`` key not in ``components`` is appended at the end (union,
    not intersection, so a newly added CLI component surfaces instead of
    silently vanishing).

    Returns:
        EnvironmentData for the ``GET /api/{template}/{name}`` response.

    """
    raw: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            raw[key.strip().removeprefix(strip_prefix)] = value.strip()

    environment_name = raw.pop("environment_name", "") or raw.pop("name", "")
    environment_root = raw.pop("environment_root", None) or None
    install_root = raw.pop("install_root", None) or None
    created_at = raw.pop("created_at", None) or None

    known: list[ComponentInfo] = []
    for comp in components:
        version = raw.pop(f"{comp}_version", None) or None
        known.append(
            ComponentInfo(name=comp, version=version, kind=_component_kind(version))
        )

    extra: list[ComponentInfo] = []
    for key in list(raw.keys()):
        if not key.endswith("_version"):
            continue
        comp = key.removesuffix("_version")
        version = raw.pop(key) or None
        extra.append(
            ComponentInfo(name=comp, version=version, kind=_component_kind(version))
        )

    all_installed = all(c.version is not None for c in known)

    return EnvironmentData(
        template=template,
        environment_name=environment_name,
        environment_root=environment_root,
        install_root=install_root,
        created_at=created_at,
        components=known + extra,
        all_installed=all_installed,
        extra=raw,
    )


# "* v1.1.14 (installed)"  -> current="*", version="v1.1.14", annotation="installed"
# "  v1.1.15"              -> current=" ", version="v1.1.15",  annotation=None
_VERSION_LINE_RE = re.compile(
    r"^(?P<current>[* ]) (?P<version>\S+)(?: \((?P<annotation>[^)]*)\))?\s*$"
)


def parse_versions_detailed(
    result: CommandResult, *, template: str, component: str
) -> VersionsData:
    """Parse ``oqtopus <subcommand> versions <component>`` into VersionsData.

    Preserves the CLI's output order verbatim: callers must not re-sort.
    Header lines (``{component}:``) don't match
    ``_VERSION_LINE_RE`` (they don't start with ``"* "``/``"  "``) and are
    skipped implicitly.

    Returns:
        VersionsData for the components/{component}/versions response.

    """
    current: str | None = None
    entries: list[VersionEntry] = []
    for raw_line in result.stdout.splitlines():
        if not raw_line.strip():
            continue
        m = _VERSION_LINE_RE.match(raw_line.rstrip())
        if not m:
            continue
        version = m.group("version")
        annotation = m.group("annotation") or ""
        kind = _component_kind(version) or "other"
        installed = "installed" in annotation
        in_remote = kind != "branch" and "not in remote tags" not in annotation
        if m.group("current") == "*":
            current = version
        entries.append(
            VersionEntry(
                version=version, kind=kind, installed=installed, in_remote=in_remote
            )
        )
    return VersionsData(
        template=template, component=component, current=current, versions=entries
    )
