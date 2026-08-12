"""Tests for the directory browser route."""

import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient

from oqtopus_manager.main import create_app


@pytest.fixture
def env_base(tmp_path: pathlib.Path) -> pathlib.Path:
    base = tmp_path / "environments"
    base.mkdir()
    return base


@pytest.fixture
def client(
    tmp_path: pathlib.Path,
    env_base: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.chdir(tmp_path)
    config = {
        "server": {
            "host": "127.0.0.1",
            "port": 8000,
            "default_environment_base_path": str(env_base),
            "environments_file": str(tmp_path / "environments.yaml"),
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
    app = create_app(cfg_path)
    return TestClient(app)


def test_browse_default_returns_200(client: TestClient) -> None:
    response = client.get("/browse")
    assert response.status_code == 200


def test_browse_shows_subdirectories(
    client: TestClient, env_base: pathlib.Path
) -> None:
    (env_base / "alpha").mkdir()
    (env_base / "beta").mkdir()
    response = client.get(f"/browse?path={env_base}")
    assert response.status_code == 200
    assert b"alpha" in response.content
    assert b"beta" in response.content


def test_browse_hides_dotfiles(client: TestClient, env_base: pathlib.Path) -> None:
    (env_base / ".hidden").mkdir()
    (env_base / "visible").mkdir()
    response = client.get(f"/browse?path={env_base}")
    assert b".hidden" not in response.content
    assert b"visible" in response.content


def test_browse_out_of_range_falls_back_to_base(client: TestClient) -> None:
    response = client.get("/browse?path=/nonexistent/path/xyz")
    assert response.status_code == 200


def test_browse_shows_parent_navigation(
    client: TestClient, env_base: pathlib.Path
) -> None:
    subdir = env_base / "subdir"
    subdir.mkdir()
    response = client.get(f"/browse?path={subdir}")
    assert response.status_code == 200
    assert b".." in response.content
