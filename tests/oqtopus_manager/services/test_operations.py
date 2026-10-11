"""Unit tests for services/operations.py (two-phase operations + ring buffer)."""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from oqtopus_manager.services.exceptions import LockBusyError, OperationNotFoundError
from oqtopus_manager.services.locks import LockRegistry, LockScope
from oqtopus_manager.services.operations import (
    Operation,
    OperationKey,
    OperationRegistry,
    OperationRequest,
)


def _request(  # ruff: ignore[too-many-arguments]
    cwd: pathlib.Path,
    script: str,
    *,
    args: tuple[str, ...] = ("install", "engine"),
    environment: str = "demo",
    components: tuple[str, ...] = ("engine",),
    timeout: float | None = None,
    started_by: str = "alice",
) -> OperationRequest:
    return OperationRequest(
        key=OperationKey(template="backend", environment=environment, args=args),
        kind=args[0],
        component=args[1] if len(args) > 1 else "engine",
        version=None,
        argv=["sh", "-c", script],
        cwd=cwd,
        timeout=timeout,
        lock_scope=LockScope(components=components, environment=environment),
        label=f"{' '.join(args)} (environment={environment})",
        started_by=started_by,
    )


async def _collect(operation: Operation) -> list[str]:
    return [chunk async for chunk in operation.subscribe()]


# ── output delivery ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_subscriber_gets_output_then_success(tmp_path: pathlib.Path) -> None:
    registry = OperationRegistry(buffer_lines=100)
    operation, joined = await registry.start(
        _request(tmp_path, "echo one; echo two"), LockRegistry()
    )
    assert joined is False
    chunks = await _collect(operation)
    assert chunks == [
        "data: one\n\n",
        "data: two\n\n",
        "event: done\ndata: success\n\n",
    ]
    assert operation.status == "success"


@pytest.mark.anyio
async def test_non_zero_exit_reports_error(tmp_path: pathlib.Path) -> None:
    registry = OperationRegistry(buffer_lines=100)
    operation, _ = await registry.start(
        _request(tmp_path, "echo boom; exit 3"), LockRegistry()
    )
    chunks = await _collect(operation)
    assert chunks[-1] == "event: done\ndata: error\n\n"
    assert operation.status == "error"


@pytest.mark.anyio
async def test_timeout_reports_timeout_and_releases_locks(
    tmp_path: pathlib.Path,
) -> None:
    locks = LockRegistry()
    registry = OperationRegistry(buffer_lines=100)
    operation, _ = await registry.start(
        _request(tmp_path, "sleep 10", timeout=0.1), locks
    )
    chunks = await _collect(operation)
    assert chunks[-1] == "event: done\ndata: timeout\n\n"
    assert operation.status == "timeout"
    assert locks.snapshot() == []


@pytest.mark.anyio
async def test_late_subscriber_replays_finished_output(tmp_path: pathlib.Path) -> None:
    registry = OperationRegistry(buffer_lines=100)
    operation, _ = await registry.start(
        _request(tmp_path, "echo one; echo two"), LockRegistry()
    )
    first = await _collect(operation)
    second = await _collect(operation)
    assert first == second


@pytest.mark.anyio
async def test_concurrent_subscribers_see_the_same_output(
    tmp_path: pathlib.Path,
) -> None:
    registry = OperationRegistry(buffer_lines=100)
    operation, _ = await registry.start(
        _request(tmp_path, "echo one; sleep 0.3; echo two"), LockRegistry()
    )
    early, late = await asyncio.gather(
        _collect(operation), _delayed_collect(operation, 0.15)
    )
    assert early == late
    assert early[-1] == "event: done\ndata: success\n\n"


async def _delayed_collect(operation: Operation, delay: float) -> list[str]:
    await asyncio.sleep(delay)
    return await _collect(operation)


@pytest.mark.anyio
async def test_buffer_keeps_only_the_last_n_lines(tmp_path: pathlib.Path) -> None:
    registry = OperationRegistry(buffer_lines=3)
    operation, _ = await registry.start(
        _request(tmp_path, "for i in 1 2 3 4 5 6 7 8 9 10; do echo $i; done"),
        LockRegistry(),
    )
    await _collect(operation)
    chunks = await _collect(operation)
    assert chunks == [
        "data: [... 7 earlier line(s) not buffered ...]\n\n",
        "data: 8\n\n",
        "data: 9\n\n",
        "data: 10\n\n",
        "event: done\ndata: success\n\n",
    ]


# ── joining vs. conflicting ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_identical_request_joins_the_running_operation(
    tmp_path: pathlib.Path,
) -> None:
    registry = OperationRegistry(buffer_lines=100)
    locks = LockRegistry()
    first, _ = await registry.start(_request(tmp_path, "sleep 0.3"), locks)
    second, joined = await registry.start(_request(tmp_path, "sleep 0.3"), locks)
    assert joined is True
    assert second is first
    await _collect(first)


@pytest.mark.anyio
async def test_identical_request_from_another_user_joins(
    tmp_path: pathlib.Path,
) -> None:
    # The key is (template, environment, CLI args): who asks doesn't matter,
    # and the operation keeps reporting the user who actually started it.
    registry = OperationRegistry(buffer_lines=100)
    locks = LockRegistry()
    first, _ = await registry.start(
        _request(tmp_path, "sleep 0.3", started_by="alice"), locks
    )
    second, joined = await registry.start(
        _request(tmp_path, "sleep 0.3", started_by="bob"), locks
    )
    assert joined is True
    assert second is first
    assert first.to_dict()["started_by"] == "alice"
    await _collect(first)


@pytest.mark.anyio
async def test_concurrent_identical_requests_join_instead_of_conflicting(
    tmp_path: pathlib.Path,
) -> None:
    registry = OperationRegistry(buffer_lines=100)
    locks = LockRegistry()
    results = await asyncio.gather(
        registry.start(_request(tmp_path, "sleep 0.2"), locks),
        registry.start(_request(tmp_path, "sleep 0.2"), locks),
    )
    (first, first_joined), (second, second_joined) = results
    assert second is first
    assert (first_joined, second_joined) == (False, True)
    await _collect(first)


@pytest.mark.anyio
async def test_different_version_conflicts_instead_of_joining(
    tmp_path: pathlib.Path,
) -> None:
    registry = OperationRegistry(buffer_lines=100)
    locks = LockRegistry()
    first, _ = await registry.start(
        _request(tmp_path, "sleep 0.3", args=("install", "engine", "v1")), locks
    )
    with pytest.raises(LockBusyError):
        await registry.start(
            _request(tmp_path, "sleep 0.3", args=("install", "engine", "v2")), locks
        )
    await _collect(first)


@pytest.mark.anyio
async def test_same_component_from_another_environment_conflicts(
    tmp_path: pathlib.Path,
) -> None:
    registry = OperationRegistry(buffer_lines=100)
    locks = LockRegistry()
    first, _ = await registry.start(_request(tmp_path, "sleep 0.3"), locks)
    with pytest.raises(LockBusyError) as excinfo:
        await registry.start(_request(tmp_path, "sleep 0.3", environment="b"), locks)
    assert excinfo.value.scope == "component:engine"
    await _collect(first)


@pytest.mark.anyio
async def test_other_component_in_same_environment_conflicts(
    tmp_path: pathlib.Path,
) -> None:
    registry = OperationRegistry(buffer_lines=100)
    locks = LockRegistry()
    first, _ = await registry.start(_request(tmp_path, "sleep 0.3"), locks)
    with pytest.raises(LockBusyError) as excinfo:
        await registry.start(
            _request(
                tmp_path,
                "sleep 0.3",
                args=("install", "gateway"),
                components=("gateway",),
            ),
            locks,
        )
    assert excinfo.value.scope == "environment:demo"
    await _collect(first)


@pytest.mark.anyio
async def test_finished_operation_is_not_joined(tmp_path: pathlib.Path) -> None:
    registry = OperationRegistry(buffer_lines=100)
    locks = LockRegistry()
    first, _ = await registry.start(_request(tmp_path, "true"), locks)
    await _collect(first)
    second, joined = await registry.start(_request(tmp_path, "true"), locks)
    assert joined is False
    assert second is not first
    await _collect(second)


# ── locks follow the subprocess, not the subscribers ─────────────────────────


@pytest.mark.anyio
async def test_locks_held_until_process_exits_without_any_subscriber(
    tmp_path: pathlib.Path,
) -> None:
    locks = LockRegistry()
    registry = OperationRegistry(buffer_lines=100)
    operation, _ = await registry.start(_request(tmp_path, "sleep 0.3"), locks)
    await asyncio.sleep(0.1)
    assert {info.scope for info in locks.snapshot()} == {
        "component:engine",
        "environment:demo",
    }
    assert all(registry.holder_of(info) is operation for info in locks.snapshot())
    await _collect(operation)
    assert locks.snapshot() == []


@pytest.mark.anyio
async def test_abandoned_subscriber_does_not_stop_the_operation(
    tmp_path: pathlib.Path,
) -> None:
    locks = LockRegistry()
    registry = OperationRegistry(buffer_lines=100)
    operation, _ = await registry.start(
        _request(tmp_path, "echo one; sleep 0.2; echo two"), locks
    )
    stream = operation.subscribe()
    assert await anext(stream) == "data: one\n\n"
    await stream.aclose()
    chunks = await _collect(operation)
    assert chunks[-2:] == ["data: two\n\n", "event: done\ndata: success\n\n"]


# ── lookup and expiry ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_and_list_are_scoped_to_the_environment(
    tmp_path: pathlib.Path,
) -> None:
    registry = OperationRegistry(buffer_lines=100)
    operation, _ = await registry.start(_request(tmp_path, "true"), LockRegistry())
    await _collect(operation)
    assert registry.get(operation.id, template="backend", environment="demo") is (
        operation
    )
    with pytest.raises(OperationNotFoundError):
        registry.get(operation.id, template="backend", environment="other")
    with pytest.raises(OperationNotFoundError):
        registry.get(operation.id, template="cloud-local", environment="demo")
    assert registry.list_for(template="backend", environment="demo") == [operation]
    assert registry.list_for(template="backend", environment="other") == []


@pytest.mark.anyio
async def test_finished_operation_expires_after_retention(
    tmp_path: pathlib.Path,
) -> None:
    registry = OperationRegistry(buffer_lines=100, retention_sec=0)
    operation, _ = await registry.start(_request(tmp_path, "true"), LockRegistry())
    await _collect(operation)
    await asyncio.sleep(0.01)
    with pytest.raises(OperationNotFoundError):
        registry.get(operation.id, template="backend", environment="demo")


@pytest.mark.anyio
async def test_only_the_latest_finished_operations_are_kept_per_environment(
    tmp_path: pathlib.Path,
) -> None:
    registry = OperationRegistry(buffer_lines=100, max_finished=2)
    locks = LockRegistry()

    async def run(environment: str) -> Operation:
        operation, _ = await registry.start(
            _request(tmp_path, "true", environment=environment), locks
        )
        await _collect(operation)
        return operation

    demo = [await run("demo") for _ in range(3)]
    other = await run("other")
    assert registry.list_for(template="backend", environment="demo") == [
        demo[2],
        demo[1],
    ]
    with pytest.raises(OperationNotFoundError):
        registry.get(demo[0].id, template="backend", environment="demo")
    # The cap is per environment: another environment's history is untouched.
    assert registry.list_for(template="backend", environment="other") == [other]


@pytest.mark.anyio
async def test_cap_never_drops_a_running_operation(tmp_path: pathlib.Path) -> None:
    registry = OperationRegistry(buffer_lines=100, max_finished=0)
    operation, _ = await registry.start(_request(tmp_path, "sleep 0.2"), LockRegistry())
    assert registry.list_for(template="backend", environment="demo") == [operation]
    await _collect(operation)
    assert registry.list_for(template="backend", environment="demo") == []


@pytest.mark.anyio
async def test_running_operation_never_expires(tmp_path: pathlib.Path) -> None:
    registry = OperationRegistry(buffer_lines=100, retention_sec=0)
    operation, _ = await registry.start(_request(tmp_path, "sleep 0.2"), LockRegistry())
    await asyncio.sleep(0.05)
    assert registry.get(operation.id, template="backend", environment="demo") is (
        operation
    )
    await _collect(operation)


@pytest.mark.anyio
async def test_to_dict_shape(tmp_path: pathlib.Path) -> None:
    registry = OperationRegistry(buffer_lines=100)
    operation, _ = await registry.start(_request(tmp_path, "true"), LockRegistry())
    await _collect(operation)
    body = operation.to_dict()
    assert body["operation_id"] == operation.id
    assert body["kind"] == "install"
    assert body["label"] == "install engine"
    assert body["status"] == "success"
    assert body["started_by"] == "alice"
    assert body["finished_at"] is not None
    assert body["lock_force_released"] is False
