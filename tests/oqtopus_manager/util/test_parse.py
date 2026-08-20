"""Unit tests for util/parse.py."""

from __future__ import annotations

from oqtopus_manager.util.cli import CommandResult
from oqtopus_manager.util.parse import (
    ServiceStatus,
    parse_device_status,
    parse_info,
    parse_service_status,
    parse_status,
    parse_versions_detailed,
)


def _ok(stdout: str) -> CommandResult:
    return CommandResult(returncode=0, stdout=stdout, stderr="")


# ── parse_service_status ────────────────────────────────────────────────────


def test_parse_service_status_parses_name_and_state() -> None:
    result = _ok("core: Running (PID 123)\ngateway: Stopped\n")
    assert parse_service_status(result) == [
        ServiceStatus(name="core", state="Running (PID 123)"),
        ServiceStatus(name="gateway", state="Stopped"),
    ]


def test_parse_service_status_skips_lines_without_colon() -> None:
    result = _ok("core: Running\nsome garbage line\n\n")
    assert parse_service_status(result) == [ServiceStatus(name="core", state="Running")]


def test_parse_service_status_empty_stdout_returns_empty_list() -> None:
    assert parse_service_status(_ok("")) == []


def test_service_status_running_is_case_insensitive() -> None:
    assert ServiceStatus(name="core", state="RUNNING (PID 1)").running is True
    assert ServiceStatus(name="core", state="running").running is True
    assert ServiceStatus(name="core", state="Stopped").running is False


# ── parse_status ─────────────────────────────────────────────────────────────


def test_parse_status_all_running_process() -> None:
    result = _ok("core: Running (PID 111)\ngateway: Running (PID 222)\n")
    data = parse_status(result, template="backend", environment_name="e1")
    assert data.template == "backend"
    assert data.environment_name == "e1"
    assert [s.model_dump() for s in data.services] == [
        {"name": "core", "kind": "process", "state": "running", "pid": 111, "containers": None},
        {"name": "gateway", "kind": "process", "state": "running", "pid": 222, "containers": None},
    ]


def test_parse_status_all_stopped() -> None:
    result = _ok("core: Stopped\ngateway: Stopped\n")
    data = parse_status(result, template="backend", environment_name="e1")
    assert [s.state for s in data.services] == ["stopped", "stopped"]
    assert all(s.pid is None for s in data.services)


def test_parse_status_mixed() -> None:
    result = _ok("core: Running (PID 1)\ngateway: Stopped\n")
    data = parse_status(result, template="backend", environment_name="e1")
    assert [s.state for s in data.services] == ["running", "stopped"]


def test_parse_status_empty_output_returns_no_services() -> None:
    data = parse_status(_ok(""), template="backend", environment_name="e1")
    assert data.services == []


def test_parse_status_container_service_running() -> None:
    result = _ok("db: Running (cloud-db-1, cloud-minio-1, cloud-mc-1)\n")
    data = parse_status(result, template="cloud-local", environment_name="e1")
    svc = data.services[0]
    assert svc.kind == "container"
    assert svc.state == "running"
    assert svc.containers == ["cloud-db-1", "cloud-minio-1", "cloud-mc-1"]
    assert svc.pid is None


def test_parse_status_container_service_stopped() -> None:
    result = _ok("db: Stopped\n")
    data = parse_status(result, template="cloud-local", environment_name="e1")
    svc = data.services[0]
    assert svc.kind == "container"
    assert svc.state == "stopped"
    assert svc.containers == []


def test_parse_status_mixed_process_and_container_in_one_output() -> None:
    result = _ok("db: Running (cloud-db-1)\nworker: Running (PID 5)\n")
    data = parse_status(result, template="cloud-local", environment_name="e1")
    assert data.services[0].kind == "container"
    assert data.services[1].kind == "process"


def test_parse_status_unknown_state_passes_through() -> None:
    result = _ok("core: Degraded\n")
    data = parse_status(result, template="backend", environment_name="e1")
    assert data.services[0].state == "degraded"


# ── parse_device_status ──────────────────────────────────────────────────────


def test_parse_device_status_active() -> None:
    data = parse_device_status(_ok("active\n"), template="backend", environment_name="e1")
    assert data.device_status == "active"


def test_parse_device_status_inactive() -> None:
    data = parse_device_status(_ok("inactive\n"), template="backend", environment_name="e1")
    assert data.device_status == "inactive"


def test_parse_device_status_maintenance() -> None:
    data = parse_device_status(_ok("maintenance\n"), template="backend", environment_name="e1")
    assert data.device_status == "maintenance"


def test_parse_device_status_maintenance_wins_over_active_substring() -> None:
    # "inactive" and "active" are both substrings that could appear in a
    # longer sentence alongside "maintenance"; maintenance must win.
    data = parse_device_status(
        _ok("device is in maintenance (was active)\n"),
        template="backend",
        environment_name="e1",
    )
    assert data.device_status == "maintenance"


def test_parse_device_status_inactive_wins_over_active_substring() -> None:
    data = parse_device_status(_ok("status: inactive\n"), template="backend", environment_name="e1")
    assert data.device_status == "inactive"


def test_parse_device_status_unrecognized_is_none() -> None:
    data = parse_device_status(_ok("garbled output\n"), template="backend", environment_name="e1")
    assert data.device_status is None


# ── parse_info ───────────────────────────────────────────────────────────────

_COMPONENTS = ("engine", "tranqu", "gateway")


def test_parse_info_all_uninstalled() -> None:
    data = parse_info(_ok(""), template="backend", components=_COMPONENTS)
    assert [c.name for c in data.components] == ["engine", "tranqu", "gateway"]
    assert all(c.version is None and c.kind is None for c in data.components)
    assert data.all_installed is False


def test_parse_info_partially_installed() -> None:
    result = _ok("engine_version=v2.1.1\n")
    data = parse_info(result, template="backend", components=_COMPONENTS)
    by_name = {c.name: c for c in data.components}
    assert by_name["engine"].version == "v2.1.1"
    assert by_name["engine"].kind == "tag"
    assert by_name["tranqu"].version is None
    assert data.all_installed is False


def test_parse_info_fully_installed() -> None:
    result = _ok(
        "engine_version=v2.1.1\ntranqu_version=v1.0.1\ngateway_version=v0.9.0\n"
    )
    data = parse_info(result, template="backend", components=_COMPONENTS)
    assert data.all_installed is True


def test_parse_info_branch_component() -> None:
    result = _ok("engine_version=branch:develop\n")
    data = parse_info(result, template="backend", components=_COMPONENTS)
    engine = next(c for c in data.components if c.name == "engine")
    assert engine.kind == "branch"


def test_parse_info_unknown_component_is_appended_not_dropped() -> None:
    result = _ok("engine_version=v1.0.0\nwidget_version=v3.0.0\n")
    data = parse_info(result, template="backend", components=_COMPONENTS)
    names = [c.name for c in data.components]
    assert names == ["engine", "tranqu", "gateway", "widget"]
    # Unknown components must not affect all_installed (only known ones do).
    assert data.all_installed is False


def test_parse_info_extra_fields_preserved() -> None:
    result = _ok("engine_version=v1.0.0\ntemplate_sha=abc123\n")
    data = parse_info(result, template="backend", components=_COMPONENTS)
    assert data.extra == {"template_sha": "abc123"}


def test_parse_info_known_metadata_fields_extracted() -> None:
    result = _ok(
        "environment_name=backend-sample\n"
        "environment_root=/env/backend-sample\n"
        "install_root=/releases\n"
        "created_at=2026-08-19T11:56:47Z\n"
    )
    data = parse_info(result, template="backend", components=_COMPONENTS)
    assert data.environment_name == "backend-sample"
    assert data.environment_root == "/env/backend-sample"
    assert data.install_root == "/releases"
    assert data.created_at == "2026-08-19T11:56:47Z"
    assert data.extra == {}


def test_parse_info_strips_prefix_for_cloud_local() -> None:
    result = _ok("cloud_local_cloud_version=v1.0.0\n")
    data = parse_info(
        result,
        template="cloud-local",
        components=("cloud", "frontend", "admin"),
        strip_prefix="cloud_local_",
    )
    cloud = next(c for c in data.components if c.name == "cloud")
    assert cloud.version == "v1.0.0"


# ── parse_versions_detailed ──────────────────────────────────────────────────


def test_parse_versions_detailed_current_marker() -> None:
    result = _ok("gateway:\n* v1.1.14 (installed)\n  v1.1.15\n")
    data = parse_versions_detailed(result, template="backend", component="gateway")
    assert data.current == "v1.1.14"
    assert data.versions[0].installed is True
    assert data.versions[1].installed is False


def test_parse_versions_detailed_not_in_remote_tags() -> None:
    result = _ok("gateway:\n  v0.9.9 (installed, not in remote tags)\n")
    data = parse_versions_detailed(result, template="backend", component="gateway")
    v = data.versions[0]
    assert v.installed is True
    assert v.in_remote is False


def test_parse_versions_detailed_in_remote_only() -> None:
    result = _ok("gateway:\n  v1.1.15 (not in remote tags)\n")
    data = parse_versions_detailed(result, template="backend", component="gateway")
    v = data.versions[0]
    assert v.installed is False
    assert v.in_remote is False


def test_parse_versions_detailed_no_annotation_defaults() -> None:
    result = _ok("gateway:\n  v1.1.15\n")
    v = parse_versions_detailed(result, template="backend", component="gateway").versions[0]
    assert v.installed is False
    assert v.in_remote is True


def test_parse_versions_detailed_branch_entry() -> None:
    result = _ok("gateway:\n  branch:develop (installed)\n")
    v = parse_versions_detailed(result, template="backend", component="gateway").versions[0]
    assert v.kind == "branch"
    assert v.in_remote is False
    assert v.installed is True


def test_parse_versions_detailed_header_line_skipped() -> None:
    result = _ok("gateway:\n  v1.0.0\n")
    data = parse_versions_detailed(result, template="backend", component="gateway")
    assert len(data.versions) == 1


def test_parse_versions_detailed_preserves_cli_order() -> None:
    result = _ok("gateway:\n  branch:develop\n  v1.1.15\n  v1.1.14\n  v0.9.9\n")
    data = parse_versions_detailed(result, template="backend", component="gateway")
    assert [v.version for v in data.versions] == [
        "branch:develop", "v1.1.15", "v1.1.14", "v0.9.9",
    ]


def test_parse_versions_detailed_no_current_marker() -> None:
    result = _ok("gateway:\n  v1.0.0\n")
    data = parse_versions_detailed(result, template="backend", component="gateway")
    assert data.current is None


def test_parse_versions_detailed_empty_output() -> None:
    data = parse_versions_detailed(_ok(""), template="backend", component="gateway")
    assert data.versions == []
    assert data.current is None
