"""The OpenAPI schema (behind the API Docs page) must build for every router.

FastAPI resolves each route's return annotation when generating the schema,
so a response class imported only under TYPE_CHECKING breaks /openapi.json
with a 500 even though every route itself still works.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient

from oqtopus_manager.main import create_app


def test_openapi_schema_builds_for_all_templates(
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
            "operation_buffer_lines": 1000,
            "file_edit_lock_timeout_sec": 600,
        },
        "appearance": {
            "app_name": "OQTOPUS Manager",
            "environment_templates": ["backend", "cloud-local"],
        },
        "auth": {
            "provider": "none",
            "none": {"default_account": "admin_user", "default_roles": ["admin"]},
        },
        "enable_debug_endpoint": True,
        "permissions": {"admin": ["environment.get"]},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config), encoding="utf-8")

    res = TestClient(create_app(tmp_path / "config.yaml")).get("/openapi.json")

    assert res.status_code == 200
    paths = res.json()["paths"]
    for template in ("backend", "cloud-local"):
        assert f"/api/{template}/{{name}}/services/{{service}}/start" in paths
        assert f"/api/{template}/{{name}}/operations/{{operation_id}}/stream" in paths
