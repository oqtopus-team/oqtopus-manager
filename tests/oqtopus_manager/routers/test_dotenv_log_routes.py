"""Integration tests for the shared dotenv and log route factories.

Both backend (/backend) and cloud-local (/cloud-local) use the same
make_dotenv_router / make_log_router factories, so one fixture set covers both.
"""

from __future__ import annotations

import pathlib
import time
import uuid

import pytest
import yaml
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from oqtopus_manager.main import create_app

_CONFIG = {
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
        "environment_templates": ["backend", "cloud-local"],
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


@pytest.fixture
def tmp_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Config + environments.yaml pre-registering backend 'demo' and cloud-local 'cl-demo'."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(_CONFIG), encoding="utf-8")
    (tmp_path / "environments.yaml").write_text(
        yaml.dump({
            "environments": [
                {"name": "demo", "template": "backend"},
                {"name": "cl-demo", "template": "cloud-local"},
            ]
        }),
        encoding="utf-8",
    )
    # Backend .env
    be_cfg = tmp_path / "environments" / "demo" / "config"
    be_cfg.mkdir(parents=True)
    (be_cfg / ".env").write_text("KEY=value\n", encoding="utf-8")
    # Cloud-local .env
    cl_cfg = tmp_path / "environments" / "cl-demo" / "config"
    cl_cfg.mkdir(parents=True)
    (cl_cfg / ".env").write_text("KEY=cl-value\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(tmp_env: pathlib.Path) -> TestClient:
    return TestClient(create_app(tmp_env / "config.yaml"), raise_server_exceptions=True)


def _dotenv_lock_path(tmp_env: pathlib.Path, env: str = "demo") -> pathlib.Path:
    return tmp_env / "environments" / env / "config" / ".env.lock"


def _write_lock(lock_path: pathlib.Path) -> str:
    tok = str(uuid.uuid4())
    lock_path.write_text(f"{tok}\n{time.time()}", encoding="utf-8")
    return tok


# ── dotenv page ──────────────────────────────────────────────────────────────


class TestDotenvPage:
    def test_renders_env_content(self, client: TestClient) -> None:
        resp = client.get("/backend/demo/dotenv")
        assert resp.status_code == 200
        assert b"KEY" in resp.content

    def test_renders_when_dotenv_missing(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        (tmp_env / "environments" / "demo" / "config" / ".env").unlink()
        resp = client.get("/backend/demo/dotenv")
        assert resp.status_code == 200

    def test_nonexistent_env_returns_404(self, client: TestClient) -> None:
        assert client.get("/backend/ghost/dotenv").status_code == 404

    def test_cloud_local_prefix_renders(self, client: TestClient) -> None:
        resp = client.get("/cloud-local/cl-demo/dotenv")
        assert resp.status_code == 200
        assert b"KEY" in resp.content

    def test_url_prefix_in_response(self, client: TestClient) -> None:
        resp = client.get("/cloud-local/cl-demo/dotenv")
        # The download link and JS fetch calls must use /cloud-local, not /backend
        assert b"/cloud-local" in resp.content
        assert b"'/backend/'" not in resp.content


# ── lock / unlock / force-unlock ─────────────────────────────────────────────


class TestDotenvLock:
    def test_acquire_returns_token(self, client: TestClient) -> None:
        resp = client.post("/backend/demo/dotenv/lock")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "token" in data

    def test_acquire_already_locked_returns_409(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        _write_lock(_dotenv_lock_path(tmp_env))
        resp = client.post("/backend/demo/dotenv/lock")
        assert resp.status_code == 409
        assert resp.json()["ok"] is False

    def test_unlock_correct_token_succeeds(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        tok = _write_lock(_dotenv_lock_path(tmp_env))
        resp = client.post("/backend/demo/dotenv/unlock", json={"token": tok})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert not _dotenv_lock_path(tmp_env).exists()

    def test_unlock_wrong_token_returns_403(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        _write_lock(_dotenv_lock_path(tmp_env))
        resp = client.post("/backend/demo/dotenv/unlock", json={"token": "wrong"})
        assert resp.status_code == 403

    def test_force_unlock_clears_lock(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        _write_lock(_dotenv_lock_path(tmp_env))
        resp = client.post("/backend/demo/dotenv/force-unlock")
        assert resp.status_code == 200
        assert not _dotenv_lock_path(tmp_env).exists()

    def test_cloud_local_lock_uses_correct_path(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        resp = client.post("/cloud-local/cl-demo/dotenv/lock")
        assert resp.status_code == 200
        cl_lock = tmp_env / "environments" / "cl-demo" / "config" / ".env.lock"
        assert cl_lock.exists()


# ── save ─────────────────────────────────────────────────────────────────────


class TestDotenvSave:
    def test_saves_content_and_creates_backup(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        tok = client.post("/backend/demo/dotenv/lock").json()["token"]
        resp = client.post(
            "/backend/demo/dotenv/save",
            json={"token": tok, "content": "KEY=updated\n"},
        )
        assert resp.json()["ok"] is True
        env_file = tmp_env / "environments" / "demo" / "config" / ".env"
        assert env_file.read_text(encoding="utf-8") == "KEY=updated\n"
        backups = list((tmp_env / "environments" / "demo" / "config").glob(".env.*"))
        assert len(backups) == 1

    def test_no_lock_returns_409(self, client: TestClient) -> None:
        resp = client.post(
            "/backend/demo/dotenv/save", json={"token": "any", "content": ""}
        )
        assert resp.status_code == 409

    def test_wrong_token_returns_403(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        _write_lock(_dotenv_lock_path(tmp_env))
        resp = client.post(
            "/backend/demo/dotenv/save", json={"token": "wrong", "content": ""}
        )
        assert resp.status_code == 403


# ── download ─────────────────────────────────────────────────────────────────


class TestDotenvDownload:
    def test_returns_file_content(self, client: TestClient) -> None:
        resp = client.get("/backend/demo/dotenv/download")
        assert resp.status_code == 200
        assert b"KEY=value" in resp.content

    def test_missing_file_returns_404(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        (tmp_env / "environments" / "demo" / "config" / ".env").unlink()
        assert client.get("/backend/demo/dotenv/download").status_code == 404

    def test_cloud_local_download(self, client: TestClient) -> None:
        resp = client.get("/cloud-local/cl-demo/dotenv/download")
        assert resp.status_code == 200
        assert b"KEY=cl-value" in resp.content

    def test_nonexistent_env_returns_404(self, client: TestClient) -> None:
        assert client.get("/backend/ghost/dotenv/download").status_code == 404


# ── log page ─────────────────────────────────────────────────────────────────


def _make_backend_log(
    tmp_env: pathlib.Path, env: str = "demo", service: str = "engine"
) -> pathlib.Path:
    """Create a backend service log file and the logging.yaml that references it."""
    env_root = tmp_env / "environments" / env
    log_path = env_root / "logs" / f"{service}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("log line 1\n", encoding="utf-8")
    cfg_dir = env_root / "config" / service
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "logging.yaml").write_text(
        yaml.dump({"handlers": {"file": {"filename": str(log_path)}}}),
        encoding="utf-8",
    )
    return log_path


def _make_cloud_local_log(
    tmp_env: pathlib.Path, env: str = "cl-demo", service: str = "worker"
) -> pathlib.Path:
    """Create a cloud-local service log file."""
    log_path = tmp_env / "environments" / env / "logs" / service / "service.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("cl log line\n", encoding="utf-8")
    return log_path


class TestLogPage:
    def test_backend_renders_when_no_logging_yaml(self, client: TestClient) -> None:
        # _get_log_file returns None when logging.yaml is absent
        resp = client.get("/backend/demo/services/engine/log")
        assert resp.status_code == 200

    def test_backend_renders_with_log_file(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        _make_backend_log(tmp_env)
        resp = client.get("/backend/demo/services/engine/log")
        assert resp.status_code == 200

    def test_nonexistent_env_returns_404(self, client: TestClient) -> None:
        assert client.get("/backend/ghost/services/engine/log").status_code == 404

    def test_cloud_local_renders(self, client: TestClient) -> None:
        resp = client.get("/cloud-local/cl-demo/services/worker/log")
        assert resp.status_code == 200
        assert b"worker" in resp.content


# ── log stream ───────────────────────────────────────────────────────────────


class TestLogStream:
    def test_backend_no_log_file_returns_404(self, client: TestClient) -> None:
        # No logging.yaml configured → _get_log_file returns None
        assert client.get("/backend/demo/services/engine/log/stream").status_code == 404

    def test_backend_stream_returns_sse(
        self, client: TestClient, tmp_env: pathlib.Path, mocker: MockerFixture
    ) -> None:
        _make_backend_log(tmp_env)

        async def _gen(*_args: object, **_kwargs: object):
            yield "data: hello\n\n"

        mocker.patch(
            "oqtopus_manager.routers._log_routes.stream_log_tail", side_effect=_gen
        )
        resp = client.get("/backend/demo/services/engine/log/stream")
        assert resp.status_code == 200
        assert b"hello" in resp.content

    def test_cloud_local_no_log_file_returns_404(self, client: TestClient) -> None:
        # logs/worker/service.log doesn't exist yet
        assert (
            client.get("/cloud-local/cl-demo/services/worker/log/stream").status_code
            == 404
        )

    def test_cloud_local_stream_returns_sse(
        self, client: TestClient, tmp_env: pathlib.Path, mocker: MockerFixture
    ) -> None:
        _make_cloud_local_log(tmp_env)

        async def _gen(*_args: object, **_kwargs: object):
            yield "data: cl-line\n\n"

        mocker.patch(
            "oqtopus_manager.routers._log_routes.stream_log_tail", side_effect=_gen
        )
        resp = client.get("/cloud-local/cl-demo/services/worker/log/stream")
        assert resp.status_code == 200
        assert b"cl-line" in resp.content


# ── log download ─────────────────────────────────────────────────────────────


class TestLogDownload:
    def test_backend_no_log_file_returns_404(self, client: TestClient) -> None:
        assert (
            client.get("/backend/demo/services/engine/log/download").status_code == 404
        )

    def test_backend_download_returns_file(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        _make_backend_log(tmp_env)
        resp = client.get("/backend/demo/services/engine/log/download")
        assert resp.status_code == 200
        assert b"log line 1" in resp.content

    def test_cloud_local_no_log_file_returns_404(self, client: TestClient) -> None:
        assert (
            client.get("/cloud-local/cl-demo/services/worker/log/download").status_code
            == 404
        )

    def test_cloud_local_download_returns_file(
        self, client: TestClient, tmp_env: pathlib.Path
    ) -> None:
        _make_cloud_local_log(tmp_env)
        resp = client.get("/cloud-local/cl-demo/services/worker/log/download")
        assert resp.status_code == 200
        assert b"cl log line" in resp.content
