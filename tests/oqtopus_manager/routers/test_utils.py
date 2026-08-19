"""Unit tests for routers/_utils.py."""

from __future__ import annotations

import pathlib

import pytest
from pytest_mock import MockerFixture

from oqtopus_manager.routers._utils import _has_running_services
from oqtopus_manager.util.cli import CommandResult


@pytest.mark.anyio
async def test_has_running_services_true_when_any_service_running(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.routers._utils.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0,
            stdout="db: Stopped\nworker: Running (PID 123)\n",
            stderr="",
        ),
    )
    assert await _has_running_services("cloud-local", tmp_path) is True


@pytest.mark.anyio
async def test_has_running_services_false_when_all_stopped(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.routers._utils.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0, stdout="db: Stopped\nworker: Stopped\n", stderr=""
        ),
    )
    assert await _has_running_services("cloud-local", tmp_path) is False


@pytest.mark.anyio
async def test_has_running_services_false_on_empty_output(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    """No components installed yet: a successful status check with nothing to report."""
    mocker.patch(
        "oqtopus_manager.routers._utils.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=0, stdout="", stderr=""),
    )
    assert await _has_running_services("backend", tmp_path) is False


@pytest.mark.anyio
async def test_has_running_services_is_case_insensitive(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.routers._utils.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0, stdout="core: RUNNING (PID 1)\n", stderr=""
        ),
    )
    assert await _has_running_services("backend", tmp_path) is True


@pytest.mark.anyio
async def test_has_running_services_fails_closed_on_command_failure(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    """A failed status check (e.g. remote fleet unreachable) must report
    "running" rather than "no services running", so callers don't mistake
    an unknown state for a safe one.
    """
    mocker.patch(
        "oqtopus_manager.routers._utils.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=1, stdout="", stderr="connection to remote fleet failed"
        ),
    )
    assert await _has_running_services("backend", tmp_path) is True


@pytest.mark.anyio
async def test_has_running_services_fails_closed_when_cli_not_found(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.routers._utils.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=127,
            stdout="",
            stderr="oqtopus command not found. Please install oqtopus-cli first.",
        ),
    )
    assert await _has_running_services("backend", tmp_path) is True
