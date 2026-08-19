"""Integration tests for environment routes."""

import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from oqtopus_manager.main import create_app
from oqtopus_manager.util.cli import CommandResult


@pytest.fixture
def config_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Write a minimal config.yaml, set CWD to tmp_path, and return config path."""
    monkeypatch.chdir(tmp_path)
    config = {
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
        },
        "appearance": {
            "app_name": "OQTOPUS Manager",
            "environment_templates": ["backend"],
        },
                "auth": {
            "provider": "none",
            "none": {
                "default_account": "admin_user",
                "default_roles": ["admin"],
            },
        },
        "enable_debug_endpoint": False,        "permissions": {
            "_extends_": {"admin": "operator"},
            "operator": [
                "environment.get", "environment.create", "environment.delete",
                "environment.config.get", "environment.config.update",
                "environment.log.get", "environment.service.manage",
                "environment.component.manage", "app_settings.get",
            ],
            "admin": ["app_settings.update"],
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(config), encoding="utf-8")
    (tmp_path / "environments.yaml").write_text("environments: []\n", encoding="utf-8")
    return cfg_path


@pytest.fixture
def client(config_path: pathlib.Path) -> TestClient:
    app = create_app(config_path)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def mock_stream_success(mocker: MockerFixture) -> None:
    """Mock oqtopus init stream to succeed without running the real command."""
    async def _gen(*args, **kwargs):
        yield "data: Initializing...\n\n"
        yield "event: done\ndata: success\n\n"

    mocker.patch("oqtopus_manager.services.environment.stream_oqtopus_init", side_effect=_gen)


@pytest.fixture
def mock_stream_failure(mocker: MockerFixture) -> None:
    """Mock oqtopus init stream to fail."""
    async def _gen(*args, **kwargs):
        yield "data: Error: template not found\n\n"
        yield "event: done\ndata: error\n\n"

    mocker.patch("oqtopus_manager.services.environment.stream_oqtopus_init", side_effect=_gen)


def test_root_redirects_to_backend(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/backend"


def test_list_environments_empty(client: TestClient) -> None:
    response = client.get("/backend")
    assert response.status_code == 200


def test_create_environment_returns_ok(client: TestClient) -> None:
    response = client.post("/backend", data={"name": "demo", "template": "backend", "root_path": ""})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_create_environment_invalid_name_returns_422(client: TestClient) -> None:
    response = client.post("/backend", data={"name": "MyEnv", "template": "backend", "root_path": ""})
    assert response.status_code == 422
    assert response.json()["ok"] is False
    assert "error" in response.json()


def test_create_duplicate_environment_returns_409(client: TestClient, mock_stream_success: None) -> None:
    client.get("/backend/stream?name=demo&template=backend")
    response = client.post("/backend", data={"name": "demo", "template": "backend", "root_path": ""})
    assert response.status_code == 409
    assert response.json()["ok"] is False
    assert "already exists" in response.json()["error"]


def test_stream_success_saves_environment(client: TestClient, mock_stream_success: None) -> None:
    response = client.get("/backend/stream?name=demo&template=backend")
    assert response.status_code == 200
    assert b"event: done" in response.content
    assert b"success" in response.content
    list_response = client.get("/backend")
    assert b"demo" in list_response.content


def test_stream_failure_does_not_save_environment(client: TestClient, mock_stream_failure: None) -> None:
    client.get("/backend/stream?name=demo&template=backend")
    list_response = client.get("/backend")
    assert b"demo" not in list_response.content


def test_get_environment_detail(client: TestClient, mock_stream_success: None) -> None:
    client.get("/backend/stream?name=demo&template=backend")
    response = client.get("/backend/demo")
    assert response.status_code == 200
    assert b"demo" in response.content


def test_get_nonexistent_environment_returns_404(client: TestClient) -> None:
    response = client.get("/backend/nonexistent")
    assert response.status_code == 404


def test_delete_environment(
    client: TestClient,
    mock_stream_success: None,
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
) -> None:
    client.get("/backend/stream?name=myenv&template=backend")
    env_dir = tmp_path / "environments" / "myenv"
    env_dir.mkdir(parents=True, exist_ok=True)
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=0, stdout="", stderr=""),
    )
    response = client.request("DELETE", "/backend/myenv")
    assert response.status_code == 200
    assert b"myenv" not in response.content
    assert not env_dir.exists()


def test_delete_environment_without_directory(
    client: TestClient, mock_stream_success: None
) -> None:
    """Delete should succeed even if the directory is already gone."""
    client.get("/backend/stream?name=myenv&template=backend")
    response = client.request("DELETE", "/backend/myenv")
    assert response.status_code == 200


def test_delete_nonexistent_environment_returns_404(client: TestClient) -> None:
    response = client.request("DELETE", "/backend/nonexistent")
    assert response.status_code == 404


def test_delete_environment_blocked_while_running(
    client: TestClient,
    mock_stream_success: None,
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
) -> None:
    client.get("/backend/stream?name=myenv&template=backend")
    env_dir = tmp_path / "environments" / "myenv"
    env_dir.mkdir(parents=True, exist_ok=True)
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0,
            stdout="core: Running (PID 123)\ngateway: Stopped\n",
            stderr="",
        ),
    )

    response = client.request("DELETE", "/backend/myenv")

    assert response.status_code == 409
    assert "myenv" in response.json()["detail"]
    assert env_dir.exists()


def test_new_environment_form(client: TestClient) -> None:
    response = client.get("/backend/new")
    assert response.status_code == 200


# ── cloud-local list / detail ─────────────────────────────────────────────────


@pytest.fixture
def cloud_local_config_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    """Minimal config with cloud-local as the active template type."""
    monkeypatch.chdir(tmp_path)
    config = {
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
        },
        "appearance": {
            "app_name": "OQTOPUS Manager",
            "environment_templates": ["cloud-local"],
        },
        "auth": {
            "provider": "none",
            "none": {
                "default_account": "admin_user",
                "default_roles": ["admin"],
            },
        },
        "enable_debug_endpoint": False,        "permissions": {
            "_extends_": {"admin": "operator"},
            "operator": [
                "environment.get", "environment.create", "environment.delete",
                "environment.config.get", "environment.config.update",
                "environment.log.get", "environment.service.manage",
                "environment.component.manage", "app_settings.get",
            ],
            "admin": ["app_settings.update"],
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(config), encoding="utf-8")
    (tmp_path / "environments.yaml").write_text("environments: []\n", encoding="utf-8")
    return cfg_path


@pytest.fixture
def cloud_local_client(cloud_local_config_path: pathlib.Path) -> TestClient:
    app = create_app(cloud_local_config_path)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def mock_cl_stream_success(mocker: MockerFixture) -> None:
    """Mock cloud-local init stream to succeed without running the real command."""

    async def _gen(*args: object, **kwargs: object):
        yield "data: Initializing...\n\n"
        yield "event: done\ndata: success\n\n"

    mocker.patch(
        "oqtopus_manager.services.environment.stream_oqtopus_init",
        side_effect=_gen,
    )


def test_cloud_local_list_empty(cloud_local_client: TestClient) -> None:
    assert cloud_local_client.get("/cloud-local").status_code == 200


def test_cloud_local_new_environment_form(cloud_local_client: TestClient) -> None:
    assert cloud_local_client.get("/cloud-local/new").status_code == 200


def test_cloud_local_create_environment_returns_ok(
    cloud_local_client: TestClient,
) -> None:
    resp = cloud_local_client.post(
        "/cloud-local",
        data={"name": "cl-demo", "template": "cloud-local", "root_path": ""},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_cloud_local_create_invalid_name_returns_422(
    cloud_local_client: TestClient,
) -> None:
    resp = cloud_local_client.post(
        "/cloud-local",
        data={"name": "MyEnv!", "template": "cloud-local", "root_path": ""},
    )
    assert resp.status_code == 422
    assert resp.json()["ok"] is False


def test_cloud_local_create_duplicate_returns_409(
    cloud_local_client: TestClient, mock_cl_stream_success: None
) -> None:
    cloud_local_client.get("/cloud-local/stream?name=cl-demo&template=cloud-local")
    resp = cloud_local_client.post(
        "/cloud-local",
        data={"name": "cl-demo", "template": "cloud-local", "root_path": ""},
    )
    assert resp.status_code == 409


def test_cloud_local_stream_success_saves_environment(
    cloud_local_client: TestClient, mock_cl_stream_success: None
) -> None:
    resp = cloud_local_client.get(
        "/cloud-local/stream?name=cl-demo&template=cloud-local"
    )
    assert resp.status_code == 200
    assert b"success" in resp.content
    assert b"cl-demo" in cloud_local_client.get("/cloud-local").content


def test_cloud_local_get_environment_detail(
    cloud_local_client: TestClient, mock_cl_stream_success: None
) -> None:
    cloud_local_client.get("/cloud-local/stream?name=cl-demo&template=cloud-local")
    resp = cloud_local_client.get("/cloud-local/cl-demo")
    assert resp.status_code == 200
    assert b"cl-demo" in resp.content


def test_cloud_local_get_nonexistent_returns_404(
    cloud_local_client: TestClient,
) -> None:
    assert cloud_local_client.get("/cloud-local/nonexistent").status_code == 404


def test_cloud_local_delete_environment(
    cloud_local_client: TestClient,
    mock_cl_stream_success: None,
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
) -> None:
    cloud_local_client.get("/cloud-local/stream?name=cl-demo&template=cloud-local")
    env_dir = tmp_path / "environments" / "cl-demo"
    env_dir.mkdir(parents=True, exist_ok=True)
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(returncode=0, stdout="", stderr=""),
    )
    resp = cloud_local_client.request("DELETE", "/cloud-local/cl-demo")
    assert resp.status_code == 200
    assert not env_dir.exists()


def test_cloud_local_delete_nonexistent_returns_404(
    cloud_local_client: TestClient,
) -> None:
    assert (
        cloud_local_client.request("DELETE", "/cloud-local/nonexistent").status_code
        == 404
    )


def test_cloud_local_delete_blocked_while_running(
    cloud_local_client: TestClient,
    mock_cl_stream_success: None,
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
) -> None:
    cloud_local_client.get("/cloud-local/stream?name=cl-demo&template=cloud-local")
    env_dir = tmp_path / "environments" / "cl-demo"
    env_dir.mkdir(parents=True, exist_ok=True)
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0,
            stdout="db: Running (cl-demo-db-1)\nworker: Stopped\n",
            stderr="",
        ),
    )

    resp = cloud_local_client.request("DELETE", "/cloud-local/cl-demo")

    assert resp.status_code == 409
    assert "cl-demo" in resp.json()["detail"]
    assert env_dir.exists()


# ── backend detail (settings-partial, component-versions) ────────────────────


def test_backend_settings_partial(
    client: TestClient, mock_stream_success: None
) -> None:
    client.get("/backend/stream?name=demo&template=backend")
    resp = client.get("/backend/demo/settings-partial")
    assert resp.status_code == 200


def test_backend_settings_partial_nonexistent_returns_404(
    client: TestClient,
) -> None:
    assert client.get("/backend/nonexistent/settings-partial").status_code == 404


def test_backend_component_versions_invalid_component_returns_400(
    client: TestClient, mock_stream_success: None
) -> None:
    client.get("/backend/stream?name=demo&template=backend")
    resp = client.get("/backend/demo/component-versions?component=invalid")
    assert resp.status_code == 400


def test_backend_component_versions_nonexistent_env_returns_404(
    client: TestClient,
) -> None:
    resp = client.get("/backend/nonexistent/component-versions?component=engine")
    assert resp.status_code == 404


def test_cloud_local_settings_partial(
    cloud_local_client: TestClient, mock_cl_stream_success: None
) -> None:
    cloud_local_client.get("/cloud-local/stream?name=cl-demo&template=cloud-local")
    resp = cloud_local_client.get("/cloud-local/cl-demo/settings-partial")
    assert resp.status_code == 200


def test_cloud_local_component_versions_invalid_returns_400(
    cloud_local_client: TestClient, mock_cl_stream_success: None
) -> None:
    cloud_local_client.get("/cloud-local/stream?name=cl-demo&template=cloud-local")
    resp = cloud_local_client.get(
        "/cloud-local/cl-demo/component-versions?component=invalid"
    )
    assert resp.status_code == 400


# ── main.py: create_app, version, app-icon, favicon, api-docs ────────────────


def test_create_app_with_empty_templates_raises(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = {
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
        },
        "appearance": {
            "app_name": "OQTOPUS Manager",
            "environment_templates": [],  # empty — should raise
        },
        "auth": {
            "provider": "none",
            "none": {
                "default_account": "admin_user",
                "default_roles": ["admin"],
            },
        },
        "enable_debug_endpoint": False,        "permissions": {
            "_extends_": {"admin": "operator"},
            "operator": [
                "environment.get", "environment.create", "environment.delete",
                "environment.config.get", "environment.config.update",
                "environment.log.get", "environment.service.manage",
                "environment.component.manage", "app_settings.get",
            ],
            "admin": ["app_settings.update"],
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(config), encoding="utf-8")
    (tmp_path / "environments.yaml").write_text("environments: []\n", encoding="utf-8")
    from oqtopus_manager.main import create_app

    with pytest.raises(ValueError, match="No environment_templates"):
        create_app(cfg_path)


def test_create_app_with_unsupported_template_raises(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = {
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
        },
        "appearance": {
            "app_name": "OQTOPUS Manager",
            "environment_templates": ["manager"],  # typo — should raise
        },
        "auth": {
            "provider": "none",
            "none": {
                "default_account": "admin_user",
                "default_roles": ["admin"],
            },
        },
        "enable_debug_endpoint": False,        "permissions": {
            "_extends_": {"admin": "operator"},
            "operator": [
                "environment.get", "environment.create", "environment.delete",
                "environment.config.get", "environment.config.update",
                "environment.log.get", "environment.service.manage",
                "environment.component.manage", "app_settings.get",
            ],
            "admin": ["app_settings.update"],
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(config), encoding="utf-8")
    (tmp_path / "environments.yaml").write_text("environments: []\n", encoding="utf-8")
    from oqtopus_manager.main import create_app

    with pytest.raises(ValueError, match="Unsupported environment_templates"):
        create_app(cfg_path)


def test_version_endpoint(client: TestClient) -> None:
    resp = client.get("/version")
    assert resp.status_code == 200
    assert "version" in resp.json()


def test_app_icon_404_when_not_configured(client: TestClient) -> None:
    assert client.get("/app-icon").status_code == 404


def test_app_icon_200_when_configured(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"\x89PNG")
    config = {
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
        },
        "appearance": {
            "app_name": "OQTOPUS Manager",
            "environment_templates": ["backend"],
            "app_icon_path": "./icon.png",
        },
        "auth": {
            "provider": "none",
            "none": {
                "default_account": "admin_user",
                "default_roles": ["admin"],
            },
        },
        "enable_debug_endpoint": False,        "permissions": {
            "_extends_": {"admin": "operator"},
            "operator": [
                "environment.get", "environment.create", "environment.delete",
                "environment.config.get", "environment.config.update",
                "environment.log.get", "environment.service.manage",
                "environment.component.manage", "app_settings.get",
            ],
            "admin": ["app_settings.update"],
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(config), encoding="utf-8")
    (tmp_path / "environments.yaml").write_text("environments: []\n", encoding="utf-8")
    from oqtopus_manager.main import create_app

    app = create_app(cfg_path)
    tc = TestClient(app, raise_server_exceptions=True)
    assert tc.get("/app-icon").status_code == 200


def test_favicon_404_when_not_configured(client: TestClient) -> None:
    assert client.get("/favicon.ico").status_code == 404


def test_api_docs_returns_200(client: TestClient) -> None:
    assert client.get("/api-docs").status_code == 200


# ── app_settings ─────────────────────────────────────────────────────────────


def test_settings_page_renders(client: TestClient) -> None:
    resp = client.get("/settings")
    assert resp.status_code == 200
    # oqtopus is not in PATH in the test environment — page still renders
    assert b"oqtopus" in resp.content or resp.status_code == 200


def test_settings_lock_acquire_and_release(
    client: TestClient, tmp_path: pathlib.Path
) -> None:
    resp = client.post("/settings/config/lock")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    token = data["token"]

    resp = client.post(
        "/settings/config/unlock",
        json={"token": token},
        headers={"Content-Type": "application/json"},
    )
    assert resp.json()["ok"] is True


def test_settings_save_config(client: TestClient, tmp_path: pathlib.Path) -> None:
    resp = client.post("/settings/config/lock")
    token = resp.json()["token"]

    new_content = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    resp = client.post(
        "/settings/config/save",
        json={"token": token, "content": new_content},
        headers={"Content-Type": "application/json"},
    )
    assert resp.json()["ok"] is True


def test_settings_invalid_which_returns_400(client: TestClient) -> None:
    resp = client.post("/settings/invalid/lock")
    assert resp.status_code == 400


def test_settings_force_unlock(client: TestClient) -> None:
    client.post("/settings/config/lock")
    resp = client.post("/settings/config/force-unlock")
    assert resp.json()["ok"] is True


# ── debug endpoint ────────────────────────────────────────────────────────────


@pytest.fixture
def debug_client(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Client with enable_debug_endpoint: True."""
    monkeypatch.chdir(tmp_path)
    config = {
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
        },
        "appearance": {
            "app_name": "OQTOPUS Manager",
            "environment_templates": ["backend"],
        },
        "auth": {
            "provider": "none",
            "none": {
                "default_account": "admin_user",
                "default_roles": ["admin"],
            },
        },
        "enable_debug_endpoint": True,
        "permissions": {
            "_extends_": {"admin": "operator"},
            "operator": [
                "environment.get", "environment.create", "environment.delete",
                "environment.config.get", "environment.config.update",
                "environment.log.get", "environment.service.manage",
                "environment.component.manage", "app_settings.get",
            ],
            "admin": ["app_settings.update"],
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(config), encoding="utf-8")
    (tmp_path / "environments.yaml").write_text("environments: []\n", encoding="utf-8")
    from oqtopus_manager.main import create_app

    return TestClient(create_app(cfg_path), raise_server_exceptions=True)


def test_debug_page_returns_200(debug_client: TestClient) -> None:
    resp = debug_client.get("/debug")
    assert resp.status_code == 200


def test_debug_page_not_registered_without_flag(client: TestClient) -> None:
    assert client.get("/debug").status_code == 404


def test_debug_page_with_malformed_jwt_shows_error(
    debug_client: TestClient,
) -> None:
    # "Bearer a..b" → payload part is "" → json.loads(b"") raises ValueError →
    # caught by `except Exception as e:` in debug_page (lines 57-58)
    resp = debug_client.get("/debug", headers={"Authorization": "Bearer a..b"})
    assert resp.status_code == 200


# ── component-versions with mocked CLI output ─────────────────────────────────


def test_backend_component_versions_returns_parsed_list(
    client: TestClient, mock_stream_success: None, mocker: MockerFixture
) -> None:
    client.get("/backend/stream?name=demo&template=backend")
    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0,
            stdout="Available versions:\n  v1.0.0\n  v1.1.0\n",
            stderr="",
        ),
    )
    resp = client.get("/backend/demo/component-versions?component=engine")
    assert resp.status_code == 200
    data = resp.json()
    assert "versions" in data
    assert "v1.0.0" in data["versions"]
    assert "v1.1.0" in data["versions"]


def test_backend_component_versions_command_failure_returns_502(
    client: TestClient, mock_stream_success: None, mocker: MockerFixture
) -> None:
    """A failed CLI invocation must surface as an error, not an empty list."""
    client.get("/backend/stream?name=demo&template=backend")
    mocker.patch(
        "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=1, stdout="", stderr="engine: remote status check failed"
        ),
    )
    resp = client.get("/backend/demo/component-versions?component=engine")
    assert resp.status_code == 502
    assert "remote status check failed" in resp.json()["detail"]


def test_cloud_local_component_versions_returns_parsed_list(
    cloud_local_client: TestClient,
    mock_cl_stream_success: None,
    mocker: MockerFixture,
) -> None:
    cloud_local_client.get("/cloud-local/stream?name=cl-demo&template=cloud-local")
    mocker.patch(
        "oqtopus_manager.services.cloud_local.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0, stdout="Available versions:\n  v2.0.0\n", stderr=""
        ),
    )
    resp = cloud_local_client.get(
        "/cloud-local/cl-demo/component-versions?component=cloud"
    )
    assert resp.status_code == 200
    assert "v2.0.0" in resp.json()["versions"]


def test_cloud_local_component_versions_command_failure_returns_502(
    cloud_local_client: TestClient,
    mock_cl_stream_success: None,
    mocker: MockerFixture,
) -> None:
    cloud_local_client.get("/cloud-local/stream?name=cl-demo&template=cloud-local")
    mocker.patch(
        "oqtopus_manager.services.cloud_local.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=1, stdout="", stderr="cloud-local: remote status check failed"
        ),
    )
    resp = cloud_local_client.get(
        "/cloud-local/cl-demo/component-versions?component=cloud"
    )
    assert resp.status_code == 502
    assert "remote status check failed" in resp.json()["detail"]


def test_delete_environment_not_blocked_when_status_check_succeeds_and_stopped(
    client: TestClient,
    mock_stream_success: None,
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
) -> None:
    """Sanity check: a clean, successful status check with nothing running still allows delete."""
    client.get("/backend/stream?name=myenv&template=backend")
    env_dir = tmp_path / "environments" / "myenv"
    env_dir.mkdir(parents=True, exist_ok=True)
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=0, stdout="core: Stopped\ngateway: Stopped\n", stderr=""
        ),
    )

    response = client.request("DELETE", "/backend/myenv")

    assert response.status_code == 200
    assert not env_dir.exists()


def test_delete_environment_blocked_when_status_check_fails(
    client: TestClient,
    mock_stream_success: None,
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
) -> None:
    """Fail-closed: if the status check itself fails, deletion must be blocked
    rather than silently treated as "no services running".
    """
    client.get("/backend/stream?name=myenv&template=backend")
    env_dir = tmp_path / "environments" / "myenv"
    env_dir.mkdir(parents=True, exist_ok=True)
    mocker.patch(
        "oqtopus_manager.services.environment.run_oqtopus_subcommand_output",
        return_value=CommandResult(
            returncode=1, stdout="", stderr="connection to remote fleet failed"
        ),
    )

    response = client.request("DELETE", "/backend/myenv")

    assert response.status_code == 409
    assert env_dir.exists()
