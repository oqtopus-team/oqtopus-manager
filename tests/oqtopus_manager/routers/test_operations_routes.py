"""Integration tests for the two-phase operation endpoints.

``POST .../install`` (and update/build-sse-runtime) answers 202 with an
operation ID; output is read from ``GET .../operations/{id}/stream``. The
CLI itself is replaced by a gated fake so each test controls when the
operation finishes.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import TYPE_CHECKING

import httpx
import pytest
import yaml

from oqtopus_manager.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from fastapi import FastAPI
    from pytest_mock import MockerFixture

    from oqtopus_manager.util.cli import CommandExit

_PERMISSIONS = {
    "_extends_": {"admin": "operator"},
    "viewer": ["environment.get"],
    "operator": [
        "environment.get",
        "environment.component.manage",
        "environment.locks.manage",
    ],
    "admin": [],
}


class _FakeCli:
    """Stands in for ``run_command``: one line, wait for the gate, one line."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.calls: list[tuple[list[str], float | None]] = []
        self.returncode = 0

    async def run(
        self,
        argv: list[str],
        _cwd: pathlib.Path,
        *,
        result: CommandExit,
        timeout: float | None = None,
        release_lock: Callable[[], Awaitable[None]] | None = None,
    ) -> AsyncGenerator[str]:
        self.calls.append((argv, timeout))
        yield "line one"
        await self.gate.wait()
        yield "line two"
        result.returncode = self.returncode
        if release_lock is not None:
            await release_lock()


def _make_app(tmp_path: pathlib.Path, templates: list[str], roles: list[str]) -> FastAPI:
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
            "oqtopus_cli_operation_timeout_sec": 123,
        },
        "appearance": {"app_name": "OQTOPUS Manager", "environment_templates": templates},
        "auth": {
            "provider": "none",
            "none": {"default_account": "alice", "default_roles": roles},
        },
        "enable_debug_endpoint": False,
        "permissions": _PERMISSIONS,
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config), encoding="utf-8")
    environments = [
        {"name": "demo", "template": "backend"},
        {"name": "demo2", "template": "backend"},
        {"name": "cl-demo", "template": "cloud-local"},
    ]
    (tmp_path / "environments.yaml").write_text(
        yaml.dump({"environments": environments}), encoding="utf-8"
    )
    for env in environments:
        (tmp_path / "environments" / env["name"]).mkdir(parents=True)
    return create_app(tmp_path / "config.yaml")


@pytest.fixture
def fake_cli(mocker: MockerFixture) -> _FakeCli:
    fake = _FakeCli()
    mocker.patch("oqtopus_manager.services.operations.run_command", new=fake.run)
    return fake


@pytest.fixture
async def client(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient]:
    monkeypatch.chdir(tmp_path)
    app = _make_app(tmp_path, ["backend", "cloud-local"], ["operator"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _install(
    client: httpx.AsyncClient, env: str = "demo", version: str = "v1"
) -> httpx.Response:
    return await client.post(
        f"/api/backend/{env}/components/engine/install", json={"version": version}
    )


# ── start + stream ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_install_returns_202_then_stream_replays_and_finishes(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    res = await _install(client)
    assert res.status_code == 202
    body = res.json()
    assert body["already_running"] is False
    assert fake_cli.calls == [(["oqtopus", "backend", "install", "engine", "v1"], 123)]

    fake_cli.gate.set()
    stream = await client.get(
        f"/api/backend/demo/operations/{body['operation_id']}/stream"
    )
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.text == (
        "data: line one\n\ndata: line two\n\nevent: done\ndata: success\n\n"
    )

    # Reconnecting after the end replays the same output (the ring buffer).
    again = await client.get(
        f"/api/backend/demo/operations/{body['operation_id']}/stream"
    )
    assert again.text == stream.text


@pytest.mark.anyio
async def test_failed_operation_streams_error_done(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    fake_cli.returncode = 1
    fake_cli.gate.set()
    op_id = (await _install(client)).json()["operation_id"]
    stream = await client.get(f"/api/backend/demo/operations/{op_id}/stream")
    assert stream.text.endswith("event: done\ndata: error\n\n")


@pytest.mark.anyio
async def test_update_and_build_sse_runtime_are_two_phase(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    fake_cli.gate.set()
    update = await client.post("/api/backend/demo/components/gateway/update")
    assert update.status_code == 202
    await client.get(
        f"/api/backend/demo/operations/{update.json()['operation_id']}/stream"
    )
    build = await client.post("/api/backend/demo/build-sse-runtime")
    assert build.status_code == 202
    assert [argv for argv, _ in fake_cli.calls] == [
        ["oqtopus", "backend", "update", "gateway"],
        ["oqtopus", "backend", "build", "sse-runtime"],
    ]


@pytest.mark.anyio
async def test_cloud_local_install_is_two_phase(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    fake_cli.gate.set()
    res = await client.post(
        "/api/cloud-local/cl-demo/components/admin/install", json={"version": ""}
    )
    assert res.status_code == 202
    op_id = res.json()["operation_id"]
    stream = await client.get(f"/api/cloud-local/cl-demo/operations/{op_id}/stream")
    assert stream.text.endswith("event: done\ndata: success\n\n")
    assert fake_cli.calls[0][0] == ["oqtopus", "cloud-local", "install", "admin"]
    # Not reachable through another template's URL.
    other = await client.get(f"/api/backend/cl-demo/operations/{op_id}/stream")
    assert other.status_code == 404


# ── joining vs. conflicting ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_identical_install_joins_the_running_operation(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    first = (await _install(client)).json()
    second = await _install(client)
    assert second.status_code == 202
    assert second.json() == {
        "operation_id": first["operation_id"],
        "already_running": True,
    }
    assert len(fake_cli.calls) == 1
    fake_cli.gate.set()


@pytest.mark.anyio
async def test_different_version_is_rejected_with_409_problem(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    await _install(client, version="v1")
    res = await _install(client, version="v2")
    assert res.status_code == 409
    assert res.headers["content-type"] == "application/problem+json"
    body = res.json()
    assert body["title"] == "Resource is locked by another operation"
    assert "component:engine" in body["detail"]
    assert "operation_id" not in body
    assert len(fake_cli.calls) == 1
    fake_cli.gate.set()


@pytest.mark.anyio
async def test_same_component_from_another_environment_is_rejected(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    await _install(client, env="demo")
    res = await _install(client, env="demo2")
    assert res.status_code == 409
    assert res.headers["content-type"] == "application/problem+json"
    fake_cli.gate.set()


# ── errors ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_unknown_environment_is_404_problem(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    res = await _install(client, env="nope")
    assert res.status_code == 404
    assert res.headers["content-type"] == "application/problem+json"
    assert fake_cli.calls == []


@pytest.mark.anyio
async def test_invalid_component_is_400_problem(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    res = await client.post(
        "/api/backend/demo/components/bogus/install", json={"version": ""}
    )
    assert res.status_code == 400
    assert res.headers["content-type"] == "application/problem+json"
    assert fake_cli.calls == []


@pytest.mark.anyio
async def test_unknown_operation_is_404_problem(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/backend/demo/operations/does-not-exist/stream")
    assert res.status_code == 404
    assert res.headers["content-type"] == "application/problem+json"
    assert res.json()["title"] == "Operation not found"


@pytest.mark.anyio
async def test_operation_is_not_visible_from_another_environment(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    fake_cli.gate.set()
    op_id = (await _install(client)).json()["operation_id"]
    res = await client.get(f"/api/backend/demo2/operations/{op_id}/stream")
    assert res.status_code == 404


# ── listing, locks and force-unlock ──────────────────────────────────────────


@pytest.mark.anyio
async def test_operations_list_reports_running_then_finished(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    op_id = (await _install(client)).json()["operation_id"]
    [running] = (await client.get("/api/backend/demo/operations")).json()
    assert running["operation_id"] == op_id
    assert running["status"] == "running"
    assert running["label"] == "install engine v1"
    assert running["version"] == "v1"
    assert running["started_by"] == "alice"
    assert (await client.get("/api/backend/demo2/operations")).json() == []

    fake_cli.gate.set()
    await client.get(f"/api/backend/demo/operations/{op_id}/stream")
    [finished] = (await client.get("/api/backend/demo/operations")).json()
    assert finished["status"] == "success"


@pytest.mark.anyio
async def test_locks_name_the_operation_and_force_unlock_flags_it(
    client: httpx.AsyncClient, fake_cli: _FakeCli
) -> None:
    op_id = (await _install(client)).json()["operation_id"]
    locks = (await client.get("/api/backend/demo/locks")).json()
    assert {lock["scope"] for lock in locks} == {
        "component:engine",
        "environment:demo",
    }
    assert {lock["operation_id"] for lock in locks} == {op_id}

    res = await client.post(
        "/api/backend/demo/locks/force-unlock", params={"scope": "component:engine"}
    )
    assert res.json() == {"ok": True}
    [operation] = (await client.get("/api/backend/demo/operations")).json()
    assert operation["status"] == "running"
    assert operation["lock_force_released"] is True

    # With the component lock gone, another environment can now start --
    # exactly the concurrency the flag warns about.
    assert (await _install(client, env="demo2")).status_code == 202
    fake_cli.gate.set()


@pytest.mark.anyio
async def test_viewer_cannot_list_or_stream_operations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = _make_app(tmp_path, ["backend"], ["viewer"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/backend/demo/operations")).status_code == 403
        assert (
            await c.get("/api/backend/demo/operations/x/stream")
        ).status_code == 403
        assert (await _install(c)).status_code == 403
