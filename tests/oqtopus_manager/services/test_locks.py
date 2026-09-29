"""Unit tests for services/locks.py (the exclusive-lock registry)."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from oqtopus_manager.services.locks import LockRegistry, no_lock


async def _hold(cm, active: list[int], max_active: list[int], hold_sec: float) -> None:
    async with cm:
        active[0] += 1
        max_active[0] = max(max_active[0], active[0])
        await asyncio.sleep(hold_sec)
        active[0] -= 1


# ── component_lock ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_component_lock_serializes_same_component() -> None:
    registry = LockRegistry()
    active, max_active = [0], [0]

    async def worker() -> None:
        cm = registry.component_lock("gateway", operation="install", held_by="u")
        await _hold(cm, active, max_active, 0.05)

    await asyncio.gather(worker(), worker())
    assert max_active[0] == 1


@pytest.mark.anyio
async def test_component_lock_allows_different_components_concurrently() -> None:
    registry = LockRegistry()
    active, max_active = [0], [0]

    async def worker(component: str) -> None:
        cm = registry.component_lock(component, operation="install", held_by="u")
        await _hold(cm, active, max_active, 0.05)

    await asyncio.gather(worker("gateway"), worker("engine"))
    assert max_active[0] == 2


# ── env_lock ─────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_env_lock_serializes_same_environment() -> None:
    registry = LockRegistry()
    active, max_active = [0], [0]

    async def worker() -> None:
        cm = registry.env_lock("qulacs", operation="install", held_by="u")
        await _hold(cm, active, max_active, 0.05)

    await asyncio.gather(worker(), worker())
    assert max_active[0] == 1


# ── service_lock / service_lock_all ──────────────────────────────────────────


@pytest.mark.anyio
async def test_service_lock_allows_different_services_in_same_env() -> None:
    registry = LockRegistry()
    active, max_active = [0], [0]

    async def worker(service: str) -> None:
        cm = registry.service_lock("qulacs", service, operation="start", held_by="u")
        await _hold(cm, active, max_active, 0.05)

    await asyncio.gather(worker("core"), worker("gateway"))
    assert max_active[0] == 2


@pytest.mark.anyio
async def test_service_lock_serializes_same_service() -> None:
    registry = LockRegistry()
    active, max_active = [0], [0]

    async def worker() -> None:
        cm = registry.service_lock("qulacs", "core", operation="start", held_by="u")
        await _hold(cm, active, max_active, 0.05)

    await asyncio.gather(worker(), worker())
    assert max_active[0] == 1


@pytest.mark.anyio
async def test_service_lock_all_blocks_individual_service_lock() -> None:
    registry = LockRegistry()
    active, max_active = [0], [0]

    async def all_worker() -> None:
        cm = registry.service_lock_all("qulacs", operation="start all", held_by="u")
        await _hold(cm, active, max_active, 0.05)

    async def single_worker() -> None:
        await asyncio.sleep(0.01)  # let all_worker acquire first
        cm = registry.service_lock("qulacs", "core", operation="start", held_by="u")
        await _hold(cm, active, max_active, 0.01)

    await asyncio.gather(all_worker(), single_worker())
    assert max_active[0] == 1


@pytest.mark.anyio
async def test_individual_service_lock_blocks_service_lock_all() -> None:
    registry = LockRegistry()
    active, max_active = [0], [0]

    async def single_worker() -> None:
        cm = registry.service_lock("qulacs", "core", operation="start", held_by="u")
        await _hold(cm, active, max_active, 0.05)

    async def all_worker() -> None:
        await asyncio.sleep(0.01)  # let single_worker acquire first
        cm = registry.service_lock_all("qulacs", operation="start all", held_by="u")
        await _hold(cm, active, max_active, 0.01)

    await asyncio.gather(single_worker(), all_worker())
    assert max_active[0] == 1


@pytest.mark.anyio
async def test_service_lock_independent_across_environments() -> None:
    registry = LockRegistry()
    active, max_active = [0], [0]

    async def worker(env: str) -> None:
        cm = registry.service_lock(env, "core", operation="start", held_by="u")
        await _hold(cm, active, max_active, 0.05)

    await asyncio.gather(worker("qulacs"), worker("cloud"))
    assert max_active[0] == 2


# ── combined locks ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_component_and_env_lock_releases_both_on_exit() -> None:
    registry = LockRegistry()
    async with registry.component_and_env_lock(
        "gateway", "qulacs", operation="install", held_by="u"
    ):
        assert len(registry.snapshot()) == 2
    assert registry.snapshot() == []


@pytest.mark.anyio
async def test_all_components_and_env_lock_two_concurrent_calls_complete() -> None:
    """Two concurrent "install all" calls must not deadlock: both acquire every
    component lock in the same (sorted) order, so one always fully precedes
    the other rather than each holding a lock the other needs.
    """
    registry = LockRegistry()
    components = ["gateway", "engine", "tranqu"]

    async def worker() -> None:
        async with registry.all_components_and_env_lock(
            components, "qulacs", operation="install all", held_by="u"
        ):
            await asyncio.sleep(0.02)

    await asyncio.wait_for(asyncio.gather(worker(), worker()), timeout=2)
    assert registry.snapshot() == []


@pytest.mark.anyio
async def test_env_all_and_env_lock_releases_both_on_exit() -> None:
    registry = LockRegistry()
    async with registry.env_all_and_env_lock("qulacs", operation="delete", held_by="u"):
        assert len(registry.snapshot()) == 2
    assert registry.snapshot() == []


# ── snapshot ──────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_snapshot_reports_scope_operation_and_holder_while_held() -> None:
    registry = LockRegistry()
    async with registry.component_lock("gateway", operation="install gateway", held_by="alice"):
        [info] = registry.snapshot()
        assert info.scope == "component:gateway"
        assert info.operation == "install gateway"
        assert info.held_by == "alice"
        assert info.held_since  # non-empty formatted timestamp
    assert registry.snapshot() == []


# ── force_unlock ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_force_unlock_component_releases_and_lets_next_waiter_in() -> None:
    registry = LockRegistry()
    entered_first = asyncio.Event()
    released_by_force = []

    async def holder() -> None:
        async with registry.component_lock("gateway", operation="op", held_by="u"):
            entered_first.set()
            await asyncio.sleep(10)  # would hang forever without force_unlock

    async def waiter() -> None:
        async with registry.component_lock("gateway", operation="op2", held_by="u2"):
            released_by_force.append(True)

    holder_task = asyncio.create_task(holder())
    waiter_task = asyncio.create_task(waiter())
    await entered_first.wait()
    await asyncio.sleep(0.01)  # let waiter() start blocking on the lock

    freed = await registry.force_unlock("component:gateway")
    assert freed is True

    await asyncio.wait_for(waiter_task, timeout=1)
    assert released_by_force == [True]
    holder_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await holder_task


@pytest.mark.anyio
async def test_force_unlock_unheld_component_returns_false() -> None:
    registry = LockRegistry()
    assert await registry.force_unlock("component:gateway") is False


@pytest.mark.anyio
async def test_force_unlock_env_lock() -> None:
    registry = LockRegistry()
    async with registry.env_lock("qulacs", operation="op", held_by="u"):
        assert await registry.force_unlock("environment:qulacs") is True
        assert registry.snapshot() == []
    # The original holder's own release must not raise even though the
    # lock was already force-cleared underneath it.


@pytest.mark.anyio
async def test_force_unlock_individual_service_scope() -> None:
    registry = LockRegistry()
    async with registry.service_lock("qulacs", "core", operation="op", held_by="u"):
        assert await registry.force_unlock("qulacs/core") is True
        assert registry.snapshot() == []


@pytest.mark.anyio
async def test_force_unlock_service_all_scope() -> None:
    registry = LockRegistry()
    async with registry.service_lock_all("qulacs", operation="op", held_by="u"):
        assert await registry.force_unlock("qulacs/*") is True
        assert registry.snapshot() == []


@pytest.mark.anyio
async def test_force_unlock_unknown_scope_format_returns_false() -> None:
    registry = LockRegistry()
    assert await registry.force_unlock("garbage") is False
    assert await registry.force_unlock("unknown-env/core") is False


# ── no_lock ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_no_lock_is_a_no_op() -> None:
    async with no_lock():
        pass
