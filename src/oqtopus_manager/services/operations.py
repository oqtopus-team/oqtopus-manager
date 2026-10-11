"""In-memory registry of long-running CLI operations (install/update/build).

These run detached from the request that started them. Their output goes
into a per-operation ring buffer that any number of subscribers can replay
and then follow live, so closing a tab or losing the connection never loses
track of an operation. For the design rationale see
docs/developer_guidelines/operations.md; for the operator-facing behavior,
docs/usage/operations.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import functools
import itertools
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from oqtopus_manager.services.exceptions import OperationNotFoundError
from oqtopus_manager.util.cli import (
    CommandExit,
    command_outcome,
    done_event,
    run_command,
)

if TYPE_CHECKING:
    import pathlib
    from collections.abc import AsyncGenerator

    from oqtopus_manager.services.locks import (
        HeldLocks,
        LockInfo,
        LockRegistry,
        LockScope,
    )

logger = logging.getLogger(__name__)

# How long a finished operation stays reachable, so someone who missed the
# end (tab closed, connection lost) can still see how it finished.
COMPLETED_RETENTION_SEC = 15 * 60
# ...but only the most recent few per environment, bounding memory use.
MAX_FINISHED_PER_ENVIRONMENT = 10


def _format_ts(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


@dataclass(frozen=True)
class OperationKey:
    """What makes two start requests "the same operation".

    *args* is the CLI argument list as requested (before the CLI resolves an
    omitted version to the current latest), so it already distinguishes the
    kind of operation, the component, the requested version and any flags.
    """

    template: str
    environment: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class OperationRequest:
    """Everything needed to start one operation."""

    key: OperationKey
    kind: str
    component: str
    version: str | None
    argv: list[str]
    cwd: pathlib.Path
    timeout: float | None
    lock_scope: LockScope
    label: str
    started_by: str


class Operation:
    """One running or recently finished operation and its output ring buffer."""

    def __init__(
        self,
        request: OperationRequest,
        held_locks: tuple[LockInfo, ...],
        buffer_lines: int,
    ) -> None:
        self.id = uuid.uuid4().hex
        self.request = request
        self.held_locks = held_locks
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.lock_force_released = False
        self._lines: deque[str] = deque(maxlen=buffer_lines)
        self._total = 0
        self._result: CommandExit | None = None
        self._condition = asyncio.Condition()

    @property
    def status(self) -> str:
        """The state: "running", "success", "error" or "timeout"."""
        if self._result is None:
            return "running"
        result = self._result
        return command_outcome(result.returncode, timed_out=result.timed_out)

    def belongs_to(self, *, template: str, environment: str) -> bool:
        """Whether this operation was started in *environment* of *template*.

        Returns:
            True if both match.

        """
        key = self.request.key
        return (key.template, key.environment) == (template, environment)

    async def append(self, line: str) -> None:
        """Add one output line, evicting the oldest once the buffer is full."""
        async with self._condition:
            self._lines.append(line)
            self._total += 1
            self._condition.notify_all()

    async def finish(self, result: CommandExit) -> None:
        """Mark the operation finished, releasing every subscriber's final wait."""
        async with self._condition:
            self._result = result
            self.finished_at = time.time()
            self._condition.notify_all()

    def _has_news(self, cursor: int) -> bool:
        return self._total > cursor or self._result is not None

    async def subscribe(self) -> AsyncGenerator[str]:
        """Replay the buffer, then follow new output until the operation finishes.

        Each subscriber keeps its own cursor (the absolute index of the next
        line it hasn't sent), so any number of them can read concurrently.

        Yields:
            Server-Sent Events-formatted strings, ending with the same
            ``event: done`` line a direct stream would have sent.

        """
        cursor = 0
        while True:
            async with self._condition:
                await self._condition.wait_for(
                    functools.partial(self._has_news, cursor)
                )
                first = self._total - len(self._lines)
                dropped = max(0, first - cursor)
                new_lines = list(
                    itertools.islice(self._lines, max(cursor, first) - first, None)
                )
                cursor = self._total
                result = self._result
            if dropped:
                yield f"data: [... {dropped} earlier line(s) not buffered ...]\n\n"
            for line in new_lines:
                yield f"data: {line}\n\n"
            if result is not None:
                yield done_event(result.returncode, timed_out=result.timed_out)
                return

    def to_dict(self) -> dict:
        """Return the JSON shape listed by ``GET .../operations``.

        Returns:
            The operation's identity, status and timestamps.

        """
        return {
            "operation_id": self.id,
            "kind": self.request.kind,
            "component": self.request.component,
            "version": self.request.version,
            "label": " ".join(self.request.key.args),
            "status": self.status,
            "started_by": self.request.started_by,
            "started_at": _format_ts(self.started_at),
            "finished_at": _format_ts(self.finished_at),
            "lock_force_released": self.lock_force_released,
        }


class OperationRegistry:
    """Process-wide registry of operations, keyed by ID and by OperationKey."""

    def __init__(
        self,
        buffer_lines: int,
        retention_sec: float = COMPLETED_RETENTION_SEC,
        max_finished: int = MAX_FINISHED_PER_ENVIRONMENT,
    ) -> None:
        self._buffer_lines = buffer_lines
        self._retention_sec = retention_sec
        self._max_finished = max_finished
        self._operations: dict[str, Operation] = {}
        self._running: dict[OperationKey, Operation] = {}
        # Serializes "look up a running duplicate, else take the locks and
        # register" so two identical requests arriving together join one
        # operation instead of the second hitting the first's locks.
        self._start_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(
        self, request: OperationRequest, locks: LockRegistry
    ) -> tuple[Operation, bool]:
        """Start the operation *request* describes, or join it if already running.

        Returns:
            The operation, and whether it was already running (joined
            rather than started).

        Raises:
            LockBusyError: If a lock it needs is held by anything else -- a
                different operation, or the same one in another environment.

        """  # ruff: ignore[docstring-extraneous-exception]
        async with self._start_lock:
            self._purge_expired()
            existing = self._running.get(request.key)
            if existing is not None:
                return existing, True
            held = await locks.try_acquire(
                request.lock_scope, operation=request.label, held_by=request.started_by
            )
            operation = Operation(request, held.infos, self._buffer_lines)
            self._operations[operation.id] = operation
            self._running[request.key] = operation
        task = asyncio.create_task(self._run(operation, held))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return operation, False

    async def _run(self, operation: Operation, held: HeldLocks) -> None:
        request = operation.request
        result = CommandExit()
        try:
            lines = run_command(
                request.argv,
                request.cwd,
                result=result,
                timeout=request.timeout,
                release_lock=held.release,
            )
            async with contextlib.aclosing(lines):
                async for line in lines:
                    await operation.append(line)
        except Exception:
            logger.exception("Operation %s (%s) failed", operation.id, request.label)
            await held.release()
            result = CommandExit(returncode=None, timed_out=False)
        finally:
            if self._running.get(request.key) is operation:
                del self._running[request.key]
            await operation.finish(result)

    def _purge_expired(self) -> None:
        """Drop finished operations past the retention time or per-environment cap."""
        cutoff = time.time() - self._retention_sec
        finished = sorted(
            (op for op in self._operations.values() if op.finished_at is not None),
            key=lambda op: op.finished_at or 0.0,
            reverse=True,
        )
        kept: dict[tuple[str, str], int] = {}
        for op in finished:
            env = (op.request.key.template, op.request.key.environment)
            count = kept.get(env, 0)
            if (op.finished_at or 0.0) < cutoff or count >= self._max_finished:
                del self._operations[op.id]
            else:
                kept[env] = count + 1

    def get(self, operation_id: str, *, template: str, environment: str) -> Operation:
        """Return the operation *operation_id*, if it belongs to this environment.

        Returns:
            The matching operation.

        Raises:
            OperationNotFoundError: If it doesn't exist, has expired, or
                belongs to a different environment.

        """
        self._purge_expired()
        operation = self._operations.get(operation_id)
        if operation is None or not operation.belongs_to(
            template=template, environment=environment
        ):
            raise OperationNotFoundError(operation_id)
        return operation

    def list_for(self, *, template: str, environment: str) -> list[Operation]:
        """Return this environment's running and recently finished operations.

        Returns:
            Newest first.

        """
        self._purge_expired()
        matching = [
            op
            for op in self._operations.values()
            if op.belongs_to(template=template, environment=environment)
        ]
        return sorted(matching, key=lambda op: op.started_at, reverse=True)

    def holder_of(self, info: LockInfo) -> Operation | None:
        """Return the running operation holding the lock *info* describes.

        Matched by identity, so a lock re-taken by someone else after a
        force-unlock is never attributed to the operation that lost it.

        Returns:
            The operation, or None if the lock isn't held by one.

        """
        for operation in self._running.values():
            if any(held is info for held in operation.held_locks):
                return operation
        return None
