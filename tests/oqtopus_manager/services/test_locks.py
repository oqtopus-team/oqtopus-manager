"""Unit tests for services/locks.py (the exclusive-lock registry)."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from oqtopus_manager.services.exceptions import LockBusyError
from oqtopus_manager.services.locks import LockRegistry, LockScope, no_lock


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


# ── environment_lock ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_environment_lock_serializes_same_environment() -> None:
    registry = LockRegistry()
    active, max_active = [0], [0]

    async def worker() -> None:
        cm = registry.environment_lock("qulacs", operation="install", held_by="u")
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
async def test_component_and_environment_lock_releases_both_on_exit() -> None:
    registry = LockRegistry()
    async with registry.component_and_environment_lock(
        "gateway", "qulacs", operation="install", held_by="u"
    ):
        assert len(registry.snapshot()) == 2
    assert registry.snapshot() == []


@pytest.mark.anyio
async def test_all_services_and_environment_lock_releases_both_on_exit() -> None:
    registry = LockRegistry()
    async with registry.all_services_and_environment_lock("qulacs", operation="delete", held_by="u"):
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
async def test_force_unlock_environment_lock() -> None:
    registry = LockRegistry()
    async with registry.environment_lock("qulacs", operation="op", held_by="u"):
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


# ── try_acquire (non-blocking) ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_try_acquire_takes_every_lock_in_scope() -> None:
    registry = LockRegistry()
    held = await registry.try_acquire(
        LockScope(components=("gateway", "engine"), environment="qulacs"),
        operation="install",
        held_by="u",
    )
    assert {info.scope for info in registry.snapshot()} == {
        "component:engine",
        "component:gateway",
        "environment:qulacs",
    }
    await held.release()
    assert registry.snapshot() == []


@pytest.mark.anyio
async def test_try_acquire_fails_fast_and_takes_nothing_when_busy() -> None:
    registry = LockRegistry()
    async with registry.environment_lock("qulacs", operation="other", held_by="bob"):
        with pytest.raises(LockBusyError) as excinfo:
            await asyncio.wait_for(
                registry.try_acquire(
                    LockScope(components=("gateway",), environment="qulacs"),
                    operation="install",
                    held_by="u",
                ),
                timeout=1,
            )
        # The component lock taken before hitting the busy one was given back.
        assert [info.scope for info in registry.snapshot()] == ["environment:qulacs"]
    assert excinfo.value.scope == "environment:qulacs"
    assert excinfo.value.holder is not None
    assert excinfo.value.holder.operation == "other"
    assert excinfo.value.status_code == 409


@pytest.mark.anyio
async def test_try_acquire_cancelled_part_way_takes_nothing() -> None:
    registry = LockRegistry()
    # Hold the environment lock's internal condition so try_acquire suspends
    # right after taking the component lock, then cancel it there.
    environment = await registry._environment("qulacs")
    async with environment._condition:
        task = asyncio.create_task(
            registry.try_acquire(
                LockScope(components=("gateway",), environment="qulacs"),
                operation="install",
                held_by="u",
            )
        )
        while not registry.snapshot():
            await asyncio.sleep(0)
        assert [info.scope for info in registry.snapshot()] == ["component:gateway"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert registry.snapshot() == []
    # Nothing lingers: the same scope can be taken right away.
    held = await registry.try_acquire(
        LockScope(components=("gateway",), environment="qulacs"),
        operation="install",
        held_by="u",
    )
    await held.release()


@pytest.mark.anyio
async def test_try_acquire_conflicts_with_itself() -> None:
    registry = LockRegistry()
    scope = LockScope(components=("engine",))
    held = await registry.try_acquire(scope, operation="build", held_by="u")
    with pytest.raises(LockBusyError):
        await registry.try_acquire(scope, operation="build", held_by="u")
    await held.release()
    await (await registry.try_acquire(scope, operation="build", held_by="u")).release()


@pytest.mark.anyio
async def test_waiting_lock_late_release_keeps_new_holder() -> None:
    registry = LockRegistry()
    async with registry.component_lock("engine", operation="first", held_by="u"):
        assert await registry.force_unlock("component:engine") is True
        second = await registry.try_acquire(
            LockScope(components=("engine",)), operation="second", held_by="u"
        )
    # "first" has now released: "second" must still hold the lock.
    [info] = registry.snapshot()
    assert info.operation == "second"
    await second.release()


@pytest.mark.anyio
async def test_service_scope_late_release_keeps_new_holder() -> None:
    registry = LockRegistry()
    second_holding = asyncio.Event()
    first_done = asyncio.Event()

    async def second() -> None:
        async with registry.service_lock("qulacs", "core", operation="second", held_by="u"):
            second_holding.set()
            await first_done.wait()
            [info] = registry.snapshot()
            assert info.operation == "second"

    async with registry.service_lock("qulacs", "core", operation="first", held_by="u"):
        assert await registry.force_unlock("qulacs/core") is True
        task = asyncio.create_task(second())
        await second_holding.wait()
    first_done.set()
    await task
    assert registry.snapshot() == []


@pytest.mark.anyio
async def test_service_all_late_release_keeps_new_holder() -> None:
    registry = LockRegistry()
    second_holding = asyncio.Event()
    first_done = asyncio.Event()

    async def second() -> None:
        async with registry.service_lock_all("qulacs", operation="second", held_by="u"):
            second_holding.set()
            await first_done.wait()
            [info] = registry.snapshot()
            assert info.operation == "second"

    async with registry.service_lock_all("qulacs", operation="first", held_by="u"):
        assert await registry.force_unlock("qulacs/*") is True
        task = asyncio.create_task(second())
        await second_holding.wait()
    first_done.set()
    await task
    assert registry.snapshot() == []


@pytest.mark.anyio
async def test_late_release_after_force_unlock_keeps_new_holder() -> None:
    registry = LockRegistry()
    scope = LockScope(components=("engine",))
    first = await registry.try_acquire(scope, operation="first", held_by="u")
    assert await registry.force_unlock("component:engine") is True
    second = await registry.try_acquire(scope, operation="second", held_by="u")
    await first.release()
    [info] = registry.snapshot()
    assert info.operation == "second"
    await second.release()
    assert registry.snapshot() == []


# ── no_lock ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_no_lock_is_a_no_op() -> None:
    async with no_lock():
        pass
