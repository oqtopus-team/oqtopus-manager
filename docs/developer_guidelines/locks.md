# Exclusive Locks

`services/locks.py` provides an in-process registry of exclusive locks guarding
`oqtopus` CLI operations that write to data shared across environments (a
component's release directory, the sse-runtime Docker image) or within one
environment (its service processes, its `.metadata`). See
[Exclusive Locks](../usage/locks.md) for the operator-facing behavior this
produces; this page is about implementing against `LockRegistry` itself.

## Design philosophy

Three independent namespaces, one per kind of shared resource:

| Method | Guards | Scope |
|---|---|---|
| `component_lock(component, ...)` | A component's release directory / Docker build | Process-wide (every environment shares one `$INSTALL_ROOT`) |
| `env_lock(env, ...)` | An environment's version binding / directory itself | One environment name |
| `service_lock(env, scope, ...)` / `service_lock_all(env, ...)` | Service start/stop/restart | Hierarchical per environment: an individual scope conflicts with `service_lock_all` for that environment, but not with other individual scopes |

Locking at the smallest resource that's actually shared is a deliberate
choice: it's what lets an install in one environment run concurrently with an
unrelated service restart in another, rather than serializing everything
behind one coarse lock.

Operations that need more than one of these must acquire them in a **fixed
order — component, then environment** — or two operations acquiring the same
pair in opposite order can deadlock. `LockRegistry` encodes this as combined
methods rather than leaving callers to get the order right themselves:

- `component_and_environment_lock(component, env, ...)` — uninstall
- `all_services_and_environment_lock(env, ...)` — delete (the whole service
  hierarchy, then the env lock itself)
- `try_acquire(LockScope(...), ...)` — install (including install all),
  update and build-sse-runtime, which must not wait (see
  [Non-blocking acquisition](#non-blocking-acquisition)). It takes the
  components sorted, then the environment, so two concurrent "install all"
  calls can't deadlock either.

**When adding a new operation that needs more than one lock, check whether it
fits one of these first. If not, add a new combined method following the same
pattern rather than composing `async with` calls at the call site** — that's
exactly the mistake this API exists to make impossible.

### Why `asyncio.Condition`, not `asyncio.Lock`

`_NamedLock` and `_EnvServiceScope` are built on `asyncio.Condition` plus a
plain `info` flag, not `asyncio.Lock`. This is what makes `force_unlock` safe:
it clears the flag and wakes waiters directly, without touching a lock
primitive's internal state. If these were built on `asyncio.Lock` instead, an
external `force_unlock` would have to call `.release()` on a lock it didn't
acquire, and when the original holder's own `finally` later called `.release()`
too, that second call would raise (`asyncio.Lock` isn't a counting semaphore).
The `finally` blocks that release these locks never raise, so a concurrent
`force_unlock` can never cause that crash. They also only clear the hold
**they** created (compared by identity with the `LockInfo` they stored). After
a force-unlock, another operation may already hold the lock again. Clearing
it unconditionally would silently drop that new hold, and a third operation
could then run concurrently with it.

## Wiring a lock into a streaming CLI call

`util/cli.py`'s `stream_oqtopus_subcommand` and `stream_oqtopus_init` both take
optional `timeout` and `lock` keyword arguments:

```python
async for chunk in stream_oqtopus_subcommand(
    "backend", args, cwd,
    timeout=cfg.oqtopus_cli_operation_timeout_sec,
    lock=lock,
):
    yield chunk
```

The important subtlety: the lock is held until the subprocess **actually
exits**, not until the SSE generator is torn down. An early client disconnect
does not release it. This matters because `_stream_command` deliberately
leaves the subprocess running in the background on disconnect (an
install-type command mid-extraction is safer left to finish than killed), so
tying the lock's release to "the browser is still listening" would let a
second operation start against the same resource while the first is still
writing to it — exactly what the lock exists to prevent.

Concretely, this is implemented as a detached supervisor task
(`_supervise_process`) that owns the subprocess's lifetime independently of
the generator: it's the one that eventually calls `process.wait()` (or kills
the process on timeout) and releases the lock, regardless of whether anyone
is still reading the SSE stream. This task is only created when `timeout` or
`lock` is passed, so callers that pass neither see no behavior change and no
extra task.

On timeout, the supervisor stops the CLI's whole **process group**, not just
the CLI: `run_command` starts every command with `start_new_session=True`,
and `_kill_process_group` sends `SIGTERM` to the group, waits up to
`_KILL_GRACE_SEC`, then sends `SIGKILL`. Killing only the CLI would leave its
children (a `docker build`, say) running and still writing to the shared
resource after the lock is released, which is exactly what the lock exists
to prevent.

"Finished" means the CLI process **itself exited**. Every wait in
`util/cli.py` uses `_wait_exited`, which polls `returncode`, never
`process.wait()`: if `process.wait()` is called before the process exits, it
also waits for every pipe to close. A process the CLI deliberately leaves
behind, such as a started service still holding stdout, would then keep the
operation "running" and its lock held indefinitely, or make a successful run
look like a timeout and get killed. Something the CLI leaves behind that still
writes to a shared resource after it exits is a CLI bug, and Manager does not
try to detect it.

If you add a new call site that needs locking, pass `lock=`/`timeout=` through
rather than wrapping the call in your own `async with` — the generator-level
`try/finally` in your router runs on disconnect too, and would release the
lock too early.

## Wiring a lock into a router

The pattern used by `routers/backend/detail.py` and
`routers/cloud_local/detail.py`:

```python
lock = backend_service.stream_lock(
    _get_lock_registry(request),   # request.app.state.lock_registry
    cmd, name, service, component,
    operation=operation,             # human-readable label, shown in GET .../locks
    held_by=_get_held_by(request),   # request.state.user.account, or "unknown"
)
```

`stream_lock` (one per service module: `services/backend.py`,
`services/cloud_local.py`) is the single place that maps a dispatcher `cmd` to
the lock it needs — the only place a new operation's locking rule should be
added. Operations that need no lock (reads) return `no_lock()`
(`contextlib.nullcontext()`), so callers never need an `if` to special-case
them.

`_get_lock_registry` and `_get_held_by` live in `routers/_utils.py`, alongside
`_get_config`/`_get_templates`.

## Non-blocking acquisition

The context-manager methods above **wait** for a busy lock. Two-phase operations
(install/update/build — see [Long-running Operations](operations.md)) must not wait: their `POST`
has to answer `202` or `409` right away. They use
`registry.try_acquire(LockScope(components=..., environment=...), ...)` instead. It takes every
lock in the scope, in the same fixed order (sorted components, then environment), or raises
`LockBusyError` naming the first busy lock after giving back any it already took. Since it never
waits, it can't deadlock with the waiting methods.

It returns a `HeldLocks`, whose `release()` is idempotent and identity-checked: after a
force-unlock lets someone else take the lock, the original holder's late `release()` leaves the
new holder alone. Each service module defines which locks an operation needs as a `LockScope`
(`install_lock_scope`, `update_lock_scope`, `build_sse_runtime_lock_scope`).

## Introspection and force-unlock

`registry.snapshot()` returns every currently-held lock as a list of
`LockInfo` (`scope`, `operation`, `held_by`, `held_since`) — this is what
backs `GET /api/{template}/{name}/locks`.

`registry.force_unlock(scope)` parses the `scope` string back into which
namespace and key it names (`"component:X"`, `"environment:X"`, or
`"{env}/{service-or-*}"`) and clears just that one lock. It cannot stop the
CLI subprocess the lock was guarding — see
[Exclusive Locks — If a lock seems stuck](../usage/locks.md#if-a-lock-seems-stuck)
for the operator-facing trade-off this implies.

## Testing

`tests/oqtopus_manager/services/test_locks.py` is the primary reference.
Concurrency assertions follow one pattern: run two `async with registry.X(...)`
blocks (each sleeping briefly while "active") via `asyncio.gather`, track the
running count in a shared list, and assert its maximum — `1` proves two calls
serialize on the same scope, `2` proves they don't block each other.

`tests/oqtopus_manager/routers/test_lock_wiring.py` covers the router-level
wiring (that a real lock and the configured timeout actually reach
`stream_oqtopus_subcommand`, and that `GET`/`POST .../locks` work end to end)
without re-testing the concurrency semantics already covered above.
