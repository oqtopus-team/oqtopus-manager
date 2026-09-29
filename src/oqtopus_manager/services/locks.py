"""In-process exclusive-lock registry guarding shared oqtopus CLI resources.

Prevents concurrent oqtopus CLI operations (install, start/stop, ...) from
corrupting data shared across environments or within one. For the design
rationale (why three separate lock namespaces, the fixed acquisition order,
the deliberate limitations) see docs/developer_guidelines/locks.md; for the
operator-facing behavior this produces, see docs/usage/locks.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@dataclass(frozen=True)
class LockInfo:
    """Snapshot of a currently-held lock, for the ``.../locks`` endpoint."""

    scope: str
    operation: str
    held_by: str
    acquired_at: float

    @property
    def held_since(self) -> str:
        """Human-readable UTC timestamp for display.

        Returns:
            The acquisition time formatted as ``YYYY-MM-DD HH:MM:SS``.

        """
        return datetime.datetime.fromtimestamp(
            self.acquired_at, tz=datetime.UTC
        ).strftime("%Y-%m-%d %H:%M:%S")


class _NamedLock:
    """A single mutex plus the metadata describing who currently holds it.

    Deliberately not built on ``asyncio.Lock`` -- see
    docs/developer_guidelines/locks.md for why ``force_release`` needs that.
    """

    def __init__(self, scope: str) -> None:
        self._scope = scope
        self._condition = asyncio.Condition()
        self.info: LockInfo | None = None

    @asynccontextmanager
    async def acquire(self, *, operation: str, held_by: str) -> AsyncGenerator[None]:
        async with self._condition:
            await self._condition.wait_for(lambda: self.info is None)
            self.info = LockInfo(
                scope=self._scope,
                operation=operation,
                held_by=held_by,
                acquired_at=time.time(),
            )
        try:
            yield
        finally:
            async with self._condition:
                self.info = None
                self._condition.notify_all()

    async def force_release(self) -> bool:
        """Clear this lock immediately, waking anyone waiting on it.

        Returns:
            True if it was actually held (and has now been cleared).

        """
        async with self._condition:
            if self.info is None:
                return False
            self.info = None
            self._condition.notify_all()
            return True


class _EnvServiceScope:
    """Hierarchical lock for one environment's service operations.

    Individual scopes (a service name, or a container-group name) can be
    held concurrently with each other, but not with the env-wide "all"
    scope, and not with another holder of the same scope name -- i.e.
    ``(env, "all")`` conflicts with any ``(env, service)``, while
    ``(env, "core")`` and ``(env, "gateway")`` don't conflict with each
    other.
    """

    def __init__(self, env: str) -> None:
        self._env = env
        self._condition = asyncio.Condition()
        self._all_info: LockInfo | None = None
        self._active: dict[str, LockInfo] = {}

    @asynccontextmanager
    async def acquire_scope(
        self, scope: str, *, operation: str, held_by: str
    ) -> AsyncGenerator[None]:
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._all_info is None and scope not in self._active
            )
            self._active[scope] = LockInfo(
                scope=f"{self._env}/{scope}",
                operation=operation,
                held_by=held_by,
                acquired_at=time.time(),
            )
        try:
            yield
        finally:
            async with self._condition:
                self._active.pop(scope, None)
                self._condition.notify_all()

    @asynccontextmanager
    async def acquire_all(
        self, *, operation: str, held_by: str
    ) -> AsyncGenerator[None]:
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._all_info is None and not self._active
            )
            self._all_info = LockInfo(
                scope=f"{self._env}/*",
                operation=operation,
                held_by=held_by,
                acquired_at=time.time(),
            )
        try:
            yield
        finally:
            async with self._condition:
                self._all_info = None
                self._condition.notify_all()

    def snapshot(self) -> list[LockInfo]:
        """Return every lock currently held in this environment's service scope.

        Returns:
            A list with the "all" lock (if held) followed by any individual
            scope locks.

        """
        infos = list(self._active.values())
        if self._all_info is not None:
            infos.append(self._all_info)
        return infos

    async def force_release(self, scope: str) -> bool:
        """Clear one scope ("*" for the "all" lock) immediately.

        Returns:
            True if *scope* was actually held (and has now been cleared).

        """
        async with self._condition:
            if scope == "*":
                if self._all_info is None:
                    return False
                self._all_info = None
            else:
                if scope not in self._active:
                    return False
                del self._active[scope]
            self._condition.notify_all()
            return True


class LockRegistry:
    """Process-wide registry of the three exclusive-lock namespaces above."""

    def __init__(self) -> None:
        self._components: dict[str, _NamedLock] = {}
        self._envs: dict[str, _NamedLock] = {}
        self._env_services: dict[str, _EnvServiceScope] = {}
        # Guards creation of the per-key lock objects above, never held
        # while an actual operation lock is being awaited.
        self._registry_lock = asyncio.Lock()

    async def _component(self, component: str) -> _NamedLock:
        async with self._registry_lock:
            return self._components.setdefault(
                component, _NamedLock(f"component:{component}")
            )

    async def _env(self, env: str) -> _NamedLock:
        async with self._registry_lock:
            return self._envs.setdefault(env, _NamedLock(f"environment:{env}"))

    async def _env_service(self, env: str) -> _EnvServiceScope:
        async with self._registry_lock:
            return self._env_services.setdefault(env, _EnvServiceScope(env))

    # ── single-resource locks ────────────────────────────────────────────

    @asynccontextmanager
    async def component_lock(
        self, component: str, *, operation: str, held_by: str
    ) -> AsyncGenerator[None]:
        """Guard a component's shared release directory / Docker build (R1/R5)."""
        lock = await self._component(component)
        async with lock.acquire(operation=operation, held_by=held_by):
            yield

    @asynccontextmanager
    async def env_lock(
        self, env: str, *, operation: str, held_by: str
    ) -> AsyncGenerator[None]:
        """Guard an environment's version binding / directory (R2/R4)."""
        lock = await self._env(env)
        async with lock.acquire(operation=operation, held_by=held_by):
            yield

    @asynccontextmanager
    async def service_lock(
        self, env: str, scope: str, *, operation: str, held_by: str
    ) -> AsyncGenerator[None]:
        """Guard one service (or container-group) within an environment (R3)."""
        state = await self._env_service(env)
        async with state.acquire_scope(scope, operation=operation, held_by=held_by):
            yield

    @asynccontextmanager
    async def service_lock_all(
        self, env: str, *, operation: str, held_by: str
    ) -> AsyncGenerator[None]:
        """Guard every service within an environment at once ("start/stop all")."""
        state = await self._env_service(env)
        async with state.acquire_all(operation=operation, held_by=held_by):
            yield

    # ── combined locks (fixed acquisition order to avoid deadlock) ──────

    @asynccontextmanager
    async def component_and_env_lock(
        self, component: str, env: str, *, operation: str, held_by: str
    ) -> AsyncGenerator[None]:
        """Guard install/uninstall/update: component first, then env, always."""
        async with (
            self.component_lock(component, operation=operation, held_by=held_by),
            self.env_lock(env, operation=operation, held_by=held_by),
        ):
            yield

    @asynccontextmanager
    async def all_components_and_env_lock(
        self, components: list[str], env: str, *, operation: str, held_by: str
    ) -> AsyncGenerator[None]:
        """Guard "install all": every component lock (fixed order), then env."""
        async with contextlib.AsyncExitStack() as stack:
            for component in sorted(components):
                await stack.enter_async_context(
                    self.component_lock(component, operation=operation, held_by=held_by)
                )
            await stack.enter_async_context(
                self.env_lock(env, operation=operation, held_by=held_by)
            )
            yield

    @asynccontextmanager
    async def env_all_and_env_lock(
        self, env: str, *, operation: str, held_by: str
    ) -> AsyncGenerator[None]:
        """Guard environment delete: the whole service hierarchy, then the env lock."""
        async with (
            self.service_lock_all(env, operation=operation, held_by=held_by),
            self.env_lock(env, operation=operation, held_by=held_by),
        ):
            yield

    # ── introspection / force-unlock ─────────────────────────────────────

    def snapshot(self) -> list[LockInfo]:
        """Return every lock currently held, across all three namespaces.

        Returns:
            A list of LockInfo, in no particular order.

        """
        infos = [lock.info for lock in self._components.values() if lock.info]
        infos += [lock.info for lock in self._envs.values() if lock.info]
        for state in self._env_services.values():
            infos.extend(state.snapshot())
        return infos

    async def force_unlock(self, scope: str) -> bool:
        """Forcibly clear the lock shown as *scope* in a ``snapshot()`` entry.

        Does not stop the CLI subprocess it belonged to -- see
        docs/usage/locks.md for the trade-off this implies.

        Returns:
            True if *scope* was actually held (and has now been cleared).

        """
        if scope.startswith("component:"):
            lock = self._components.get(scope.removeprefix("component:"))
            return await lock.force_release() if lock else False
        if scope.startswith("environment:"):
            lock = self._envs.get(scope.removeprefix("environment:"))
            return await lock.force_release() if lock else False
        env, sep, sub = scope.partition("/")
        state = self._env_services.get(env)
        if not sep or state is None:
            return False
        return await state.force_release(sub)


def no_lock() -> AbstractAsyncContextManager[None]:
    """Return a no-op lock for operations that need none (device-status, versions).

    Returns:
        An async context manager that does nothing.

    """
    return contextlib.nullcontext()
