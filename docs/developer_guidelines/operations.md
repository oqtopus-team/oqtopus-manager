# Long-running Operations

`services/operations.py` runs install, update and build-sse-runtime **detached from the request
that started them**, and keeps their output so it can be read later, by anyone, any number of
times. See [Long-running Operations](../usage/operations.md) for the operator-facing behavior; this
page covers the implementation.

## Why two phases

Up to the dedicated-endpoint refactor, running an operation and reading its output shared a
single HTTP connection: `POST .../install` returned the output stream itself. The subprocess
already survived a disconnect (see [Exclusive Locks](locks.md#wiring-a-lock-into-a-streaming-cli-call)),
but its output went nowhere once the connection closed, so nobody could see the progress any more.

Starting and reading are now separate:

```text
POST .../components/{component}/install   → 202 {"operation_id": "...", "already_running": false}
GET  .../operations/{operation_id}/stream → buffered output, then live output, then event: done
```

Only operations that routinely outlast a page visit are two-phase. The deciding factor is how long
an operation takes, not which lock or permission it uses. `uninstall` takes the same locks and
permission as `install` but finishes almost instantly, so it still streams its output directly
in the `POST` response, like start/stop/restart and `init`.

## Pieces

| Piece | Responsibility |
|---|---|
| `OperationRegistry` (`app.state.operation_registry`) | Starts, finds and expires operations. Separate from `LockRegistry`: locks are about exclusion, operations about keeping and serving output |
| `Operation` | One run: its request, the locks it holds, its status, and a ring buffer of its last `operation_buffer_lines` output lines |
| `OperationKey` | `(template, environment, args)`: what makes two requests "the same operation" |
| `LockRegistry.try_acquire` | Takes every lock an operation needs without waiting, or none of them (see [Exclusive Locks](locks.md#non-blocking-acquisition)) |
| `util.cli.run_command` | Runs the subprocess and yields its output lines. Shared with the direct-stream path (`_stream_command`); only where the lines go differs |
| `routers/_operations.py` | `start_operation` (the 202 response), the `GET .../operations[/{id}/stream]` routes, and the lock-list / force-unlock responses that link locks to operations |

## Starting: join, conflict, or start

`OperationRegistry.start` does the following while holding `_start_lock`:

1. If an operation with the same `OperationKey` is still running, return it (`already_running: true`).
2. Otherwise call `LockRegistry.try_acquire`. If any lock is busy, `LockBusyError` → `409`, and no
   operation is created.
3. Otherwise register a new operation and spawn a task that runs it.

Doing steps 1–3 under one lock matters: two identical requests arriving together must both see
the same operation, not have the second one fail on the first one's locks.

The key uses the CLI arguments **as requested**, not as the CLI later resolves them. An omitted
version stays omitted (shown as `"latest"`), so two "latest" requests join each other even if the
remote latest changes in between. Two different explicit versions never join; they conflict on the
component lock. Because the key is the full argument list, it also covers the operation kind and
flags such as `--skip-sse-build`: an install with that flag never joins one without it.

Only *running* operations are joined. Once one finishes, the same request starts a new operation.

## The ring buffer

Each `Operation` stores output in a `deque(maxlen=operation_buffer_lines)` plus a running total of
lines ever appended. A subscriber keeps its own cursor: the absolute index of the next line it
hasn't sent yet. When it wakes up it computes which of its pending lines are still in the deque,
sends those, and reports how many were already evicted. Writers `append()` under an
`asyncio.Condition` and `notify_all()`, and subscribers `wait_for` new lines or completion. This is
the same pattern `_NamedLock` uses in `services/locks.py`.

Completion is a separate field, not a buffered line, so it can never be evicted. After draining,
a subscriber sends the same `event: done` line (`util.cli.done_event`) that the direct-stream
path sends, so the browser parses both the same way.

A subscriber disconnecting only closes its own generator. The writer task and the subprocess are
unaffected.

## Lifetime and locks

The locks taken in step 2 are released by `run_command`'s supervisor task when the subprocess
**exits**, normally or killed at `oqtopus_cli_operation_timeout_sec`. This is the same rule as the
direct-stream path. The operation leaves the "running" index once its output has been fully read,
and the registry keeps it for `COMPLETED_RETENTION_SEC` (15 minutes) after it finishes, but only
the `MAX_FINISHED_PER_ENVIRONMENT` (10) most recently finished per environment. That caps memory
at roughly 10 × `operation_buffer_lines` lines per environment. Running operations are never
dropped. Purging happens lazily on each `start`/`get`/`list_for` call; there is no background task.

Everything lives in process memory. A restart loses every operation, the same as with locks.

## Force-unlock

`HeldLocks` remembers the exact `LockInfo` objects it created. `OperationRegistry.holder_of(info)`
matches by identity, so a lock that another operation took after a force-unlock is never
attributed to the operation that lost it. `force_unlock_response` uses this to set
`lock_force_released` on the operation, and `locks_response` uses it to report `operation_id` per
lock. Releasing also checks identity (`_NamedLock.release`), so the original holder's late release
can't clear the new holder's lock.

## Testing

- `tests/oqtopus_manager/services/test_operations.py`: the registry and ring buffer, using real
  `sh -c` subprocesses (replay, concurrent subscribers, eviction, join vs. conflict, expiry, locks
  held without any subscriber).
- `tests/oqtopus_manager/routers/test_operations_routes.py`: the HTTP contract (202/404/409 and
  `application/problem+json`, permissions, the lock/force-unlock linkage). It uses
  `httpx.ASGITransport` in the test's event loop so the operation's background task outlives each
  request, and a gated fake in place of `run_command`.
