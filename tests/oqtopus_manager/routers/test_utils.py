"""Unit tests for routers/_utils.py."""

from __future__ import annotations

import pathlib

import pytest
from pytest_mock import MockerFixture

from oqtopus_manager.routers._utils import _has_running_services


@pytest.mark.anyio
async def test_has_running_services_true_when_any_service_running(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.routers._utils.run_oqtopus_subcommand_output",
        return_value="db: Stopped\nworker: Running (PID 123)\n",
    )
    assert await _has_running_services("cloud-local", tmp_path) is True


@pytest.mark.anyio
async def test_has_running_services_false_when_all_stopped(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.routers._utils.run_oqtopus_subcommand_output",
        return_value="db: Stopped\nworker: Stopped\n",
    )
    assert await _has_running_services("cloud-local", tmp_path) is False


@pytest.mark.anyio
async def test_has_running_services_false_on_empty_output(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    """No components installed yet, or the CLI is unavailable: nothing to report."""
    mocker.patch(
        "oqtopus_manager.routers._utils.run_oqtopus_subcommand_output",
        return_value="",
    )
    assert await _has_running_services("backend", tmp_path) is False


@pytest.mark.anyio
async def test_has_running_services_is_case_insensitive(
    tmp_path: pathlib.Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.routers._utils.run_oqtopus_subcommand_output",
        return_value="core: RUNNING (PID 1)\n",
    )
    assert await _has_running_services("backend", tmp_path) is True
