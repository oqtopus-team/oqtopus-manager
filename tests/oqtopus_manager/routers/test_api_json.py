"""Integration tests for the JSON endpoints under /api.

Covers: status, device-status, info (environment resource),
components/{c}/versions, the aggregated list, RFC 9457 error responses,
and reserved-name rejection.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from oqtopus_manager.main import create_app
from oqtopus_manager.util.cli import CommandResult

_PERMISSIONS = {
    "_extends_": {"admin": "operator"},
    "operator": [
        "environment.get", "environment.create", "environment.delete",
        "environment.config.get", "environment.config.update",
        "environment.log.get", "environment.service.manage",
        "environment.component.manage", "app_settings.get",
    ],
    "admin": ["app_settings.update"],
}


def _config(templates: list[str]) -> dict:
    return {
        "server": {
            "host": "127.0.0.1",
            "port": 8000,
            "default_environment_base_path": "./environments",
            "environments_file": "./environments.yaml",
        },
        "behavior": {
            "log_tail_lines": 100,
            "log_buffer_lines": 1000,
            "file_edit_lock_timeout_sec": 600,
            "oqtopus_cli_timeout_sec": 10,
        },
        "appearance": {
            "app_name": "OQTOPUS Manager",
            "environment_templates": templates,
        },
        "auth": {
            "provider": "none",
            "none": {"default_account": "admin_user", "default_roles": ["admin"]},
        },
        "enable_debug_endpoint": False,
        "permissions": _PERMISSIONS,
    }


@pytest.fixture
def backend_client(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.dump(_config(["backend"])), encoding="utf-8"
    )
    (tmp_path / "environments.yaml").write_text(
        yaml.dump({"environments": [{"name": "demo", "template": "backend"}]}),
        encoding="utf-8",
    )
    (tmp_path / "environments" / "demo").mkdir(parents=True)
    return TestClient(
        create_app(tmp_path / "config.yaml"), raise_server_exceptions=True
    )


@pytest.fixture
def cloud_local_client(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.dump(_config(["cloud-local"])), encoding="utf-8"
    )
    (tmp_path / "environments.yaml").write_text(
        yaml.dump({"environments": [{"name": "cl-demo", "template": "cloud-local"}]}),
        encoding="utf-8",
    )
    (tmp_path / "environments" / "cl-demo").mkdir(parents=True)
    return TestClient(
        create_app(tmp_path / "config.yaml"), raise_server_exceptions=True
    )


# ── status ───────────────────────────────────────────────────────────────────


def test_get_status_returns_parsed_services(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0, stdout="core: Running (PID 42)\ngateway: Stopped\n", stderr=""
        ),
    )
    resp = backend_client.get("/api/backend/demo/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["template"] == "backend"
    assert data["environment_name"] == "demo"
    assert data["services"][0] == {
        "name": "core", "kind": "process", "state": "running",
        "pid": 42, "containers": None,
    }


def test_get_status_nonexistent_env_returns_404(backend_client: TestClient) -> None:
    resp = backend_client.get("/api/backend/ghost/status")
    assert resp.status_code == 404


def test_get_status_command_failure_returns_problem_json(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=1, stdout="", stderr="boom"),
    )
    resp = backend_client.get("/api/backend/demo/status")
    assert resp.status_code == 502
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["title"] == "CLI command failed"
    assert body["status"] == 502
    assert body["returncode"] == 1


def test_get_status_cli_not_found_reports_returncode_127(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=127, stdout="", stderr="not found"),
    )
    resp = backend_client.get("/api/backend/demo/status")
    assert resp.status_code == 502
    assert resp.json()["returncode"] == 127


def test_get_status_timeout_returns_504(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=None, stdout="", stderr="timed out"),
    )
    resp = backend_client.get("/api/backend/demo/status")
    assert resp.status_code == 504


# ── device-status ────────────────────────────────────────────────────────────


def test_get_device_status_returns_parsed_value(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=0, stdout="active\n", stderr=""),
    )
    resp = backend_client.get("/api/backend/demo/device-status")
    assert resp.status_code == 200
    assert resp.json()["device_status"] == "active"


# ── info (environment resource) ───────────────────────────────────────────────


def test_get_environment_info_lists_all_known_components(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0, stdout="engine_version=v1.0.0\n", stderr=""
        ),
    )
    resp = backend_client.get("/api/backend/demo")
    assert resp.status_code == 200
    data = resp.json()
    assert [c["name"] for c in data["components"]] == ["engine", "tranqu", "gateway"]
    assert data["all_installed"] is False


# ── aggregated list ────────────────────────────────────────────────────────────


def test_list_json_without_include_status_omits_status(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=0, stdout="", stderr=""),
    )
    resp = backend_client.get("/api/backend")
    assert resp.status_code == 200
    data = resp.json()
    assert data["template"] == "backend"
    entry = data["environments"][0]
    assert entry["name"] == "demo"
    assert entry["environment"]["data"]["all_installed"] is False
    assert entry["status"] is None


def test_list_json_include_status_fetches_status_when_fully_installed(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    async def _fake_run(subcommand: str, args: list[str], cwd: object, timeout: float):
        if args == ["info"]:
            return CommandResult(
                returncode=0,
                stdout=(
                    "engine_version=v1.0.0\n"
                    "tranqu_version=v1.0.0\n"
                    "gateway_version=v1.0.0\n"
                ),
                stderr="",
            )
        if args == ["status"]:
            return CommandResult(returncode=0, stdout="core: Running (PID 1)\n", stderr="")
        if args == ["device-status", "show"]:
            return CommandResult(returncode=0, stdout="active\n", stderr="")
        msg = f"unexpected args {args}"
        raise AssertionError(msg)

    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        side_effect=_fake_run,
    )
    resp = backend_client.get("/api/backend?include_status=true")
    assert resp.status_code == 200
    entry = resp.json()["environments"][0]
    assert entry["status"]["data"]["services"][0]["state"] == "running"
    assert entry["device_status"]["data"]["device_status"] == "active"


def test_list_json_partial_failure_does_not_break_other_entries(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.dump(_config(["backend"])), encoding="utf-8"
    )
    (tmp_path / "environments.yaml").write_text(
        yaml.dump({
            "environments": [
                {"name": "broken", "template": "backend"},
                {"name": "ok", "template": "backend"},
            ]
        }),
        encoding="utf-8",
    )
    (tmp_path / "environments" / "broken").mkdir(parents=True)
    (tmp_path / "environments" / "ok").mkdir(parents=True)
    client = TestClient(
        create_app(tmp_path / "config.yaml"), raise_server_exceptions=True
    )

    async def _fake_run(subcommand: str, args: list[str], cwd: object, timeout: float):
        if "broken" in str(cwd):
            return CommandResult(returncode=1, stdout="", stderr="broken env")
        return CommandResult(returncode=0, stdout="", stderr="")

    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        side_effect=_fake_run,
    )
    resp = client.get("/api/backend")
    assert resp.status_code == 200
    by_name = {e["name"]: e for e in resp.json()["environments"]}
    assert "error" in by_name["broken"]["environment"]
    assert by_name["broken"]["environment"]["error"]["returncode"] == 1
    assert by_name["ok"]["environment"]["data"] is not None


def test_cloud_local_list_json_has_no_device_status_key(
    cloud_local_client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.cloud_local.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=0, stdout="", stderr=""),
    )
    resp = cloud_local_client.get("/api/cloud-local")
    assert resp.status_code == 200
    entry = resp.json()["environments"][0]
    assert "device_status" not in entry


# ── reserved names ───────────────────────────────────────────────────────────


def test_create_environment_rejects_reserved_name(backend_client: TestClient) -> None:
    resp = backend_client.post(
        "/api/backend", data={"name": "new", "template": "backend", "root_path": ""}
    )
    assert resp.status_code == 422
    assert resp.json()["ok"] is False


def test_create_environment_rejects_stream(backend_client: TestClient) -> None:
    resp = backend_client.post(
        "/api/backend", data={"name": "stream", "template": "backend", "root_path": ""}
    )
    assert resp.status_code == 422
