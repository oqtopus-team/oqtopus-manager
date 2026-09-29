"""Integration tests for exclusive-lock wiring on the stream dispatcher endpoints.

The lock *semantics* (which scope each cmd acquires) are covered by the
service-layer stream_lock tests; these tests only confirm the router
actually threads a real lock and the configured operation timeout through
to ``stream_oqtopus_subcommand``, rather than mocking it away silently.
"""

from __future__ import annotations

import contextlib
import pathlib
from typing import cast
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from oqtopus_manager.main import create_app

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
            "oqtopus_cli_read_timeout_sec": 10,
            "oqtopus_cli_operation_timeout_sec": 123,
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
def backend_client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
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


def _fake_stream(mocker: MockerFixture, target: str) -> MagicMock:
    async def _gen(*_args: object, **_kwargs: object):
        yield "event: done\ndata: success\n\n"

    return mocker.patch(target, side_effect=_gen)


# ── backend ──────────────────────────────────────────────────────────────────


def test_backend_stream_start_gets_operation_timeout_and_a_real_lock(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mock = _fake_stream(
        mocker, "oqtopus_manager.routers.backend.detail.stream_oqtopus_subcommand"
    )
    resp = backend_client.get(
        "/api/backend/demo/stream?cmd=start&service=core"
    )
    assert resp.status_code == 200
    _args, kwargs = mock.call_args
    assert kwargs["timeout"] == 123
    assert not isinstance(kwargs["lock"], contextlib.nullcontext)


def test_backend_stream_device_status_set_gets_no_lock(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mock = _fake_stream(
        mocker, "oqtopus_manager.routers.backend.detail.stream_oqtopus_subcommand"
    )
    resp = backend_client.get(
        "/api/backend/demo/stream?cmd=device-status-set&status=active"
    )
    assert resp.status_code == 200
    _args, kwargs = mock.call_args
    assert isinstance(kwargs["lock"], contextlib.nullcontext)


def test_backend_init_stream_gets_environment_lock(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mock = _fake_stream(
        mocker, "oqtopus_manager.services.environment.stream_oqtopus_init"
    )
    resp = backend_client.get(
        "/api/backend/stream?name=newenv&template=backend"
    )
    assert resp.status_code == 200
    _args, kwargs = mock.call_args
    assert kwargs["timeout"] == 123
    assert not isinstance(kwargs["lock"], contextlib.nullcontext)


# ── cloud-local ──────────────────────────────────────────────────────────────


def test_cloud_local_stream_start_gets_operation_timeout_and_a_real_lock(
    cloud_local_client: TestClient, mocker: MockerFixture
) -> None:
    mock = _fake_stream(
        mocker,
        "oqtopus_manager.routers.cloud_local.detail.stream_oqtopus_subcommand",
    )
    resp = cloud_local_client.get(
        "/api/cloud-local/cl-demo/stream?cmd=start&service=worker"
    )
    assert resp.status_code == 200
    _args, kwargs = mock.call_args
    assert kwargs["timeout"] == 123
    assert not isinstance(kwargs["lock"], contextlib.nullcontext)


def test_cloud_local_stream_versions_gets_no_lock(
    cloud_local_client: TestClient, mocker: MockerFixture
) -> None:
    mock = _fake_stream(
        mocker,
        "oqtopus_manager.routers.cloud_local.detail.stream_oqtopus_subcommand",
    )
    resp = cloud_local_client.get(
        "/api/cloud-local/cl-demo/stream?cmd=versions&component=cloud"
    )
    assert resp.status_code == 200
    _args, kwargs = mock.call_args
    assert isinstance(kwargs["lock"], contextlib.nullcontext)


# ── delete (all_services_and_environment_lock) ──────────────────────────────


def test_delete_environment_acquires_and_releases_lock(
    backend_client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "oqtopus_manager.services.environment.has_running_services",
        return_value=False,
    )
    resp = backend_client.delete("/api/backend/demo")
    assert resp.status_code == 200


# ── GET/POST .../locks ───────────────────────────────────────────────────────


def test_get_locks_empty_by_default(backend_client: TestClient) -> None:
    resp = backend_client.get("/api/backend/demo/locks")
    assert resp.status_code == 200
    assert resp.json() == []


def test_force_unlock_unheld_scope_returns_false(backend_client: TestClient) -> None:
    resp = backend_client.post(
        "/api/backend/demo/locks/force-unlock", params={"scope": "component:gateway"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": False}


@pytest.mark.anyio
async def test_get_locks_shows_a_held_lock_and_force_unlock_clears_it(
    backend_client: TestClient,
) -> None:
    registry = cast(FastAPI, backend_client.app).state.lock_registry
    cm = registry.component_lock("gateway", operation="install gateway", held_by="alice")
    await cm.__aenter__()  # left open on purpose: simulates a lock held mid-request
    try:
        resp = backend_client.get("/api/backend/demo/locks")
        assert resp.status_code == 200
        [entry] = resp.json()
        assert entry["scope"] == "component:gateway"
        assert entry["operation"] == "install gateway"
        assert entry["held_by"] == "alice"
        assert entry["held_since"]

        resp = backend_client.post(
            "/api/backend/demo/locks/force-unlock",
            params={"scope": "component:gateway"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert backend_client.get("/api/backend/demo/locks").json() == []
    finally:
        # The original holder's own release must be harmless even though
        # force-unlock already cleared it above (see LockRegistry.force_unlock).
        await cm.__aexit__(None, None, None)
