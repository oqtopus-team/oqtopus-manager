"""Unit tests for util/parse.py."""

from __future__ import annotations

from oqtopus_manager.util.cli import CommandResult
from oqtopus_manager.util.parse import ServiceStatus, parse_service_status, parse_versions


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


# ── parse_versions ───────────────────────────────────────────────────────────


def test_parse_versions_extracts_semver_tags() -> None:
    result = _ok("Available versions:\n  v1.0.0\n  v1.1.0\n")
    assert parse_versions(result) == ["v1.0.0", "v1.1.0"]


def test_parse_versions_extracts_branch_tokens() -> None:
    result = _ok("Available versions:\n  branch:main\n  branch:feature/foo\n  v2.0.0\n")
    assert parse_versions(result) == ["branch:main", "branch:feature/foo", "v2.0.0"]


def test_parse_versions_empty_stdout_returns_empty_list() -> None:
    assert parse_versions(_ok("")) == []


def test_parse_versions_ignores_unmatched_lines() -> None:
    result = _ok("Available versions:\n  (none installed)\n")
    assert parse_versions(result) == []
