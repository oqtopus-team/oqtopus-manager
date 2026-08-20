"""Environment business logic shared by all template types."""

from __future__ import annotations

import asyncio
import datetime
import logging
import pathlib
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ValidationError

from oqtopus_manager.models.environment import Environment
from oqtopus_manager.services.exceptions import (
    CliNotFoundError,
    CliTimeoutError,
    CommandFailedError,
    EnvironmentAlreadyExistsError,
    EnvironmentNotFoundError,
    EnvironmentValidationError,
    LockConflictError,
    LockExpiredError,
    LockNotHeldError,
    LockTokenMismatchError,
    LogFileNotFoundError,
    ReservedEnvironmentNameError,
    ServiceError,
    ServicesStillRunningError,
)
from oqtopus_manager.util.cli import (
    CommandResult,
    run_oqtopus_subcommand_output,
    stream_oqtopus_init,
)
from oqtopus_manager.util.parse import parse_service_status

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from oqtopus_manager.config import AppConfig

# GET /api/{template}?include_status=true issues up to N + 2M subprocesses
# (N environments' info + M installed environments' status/device-status).
# Bounded to avoid fork-cost blowup on hosts with many environments; tune
# only if this proves to be a real bottleneck in practice.
_LIST_FETCH_CONCURRENCY = 8

logger = logging.getLogger(__name__)

# First-path-segment route names that collide with `/{template}/{name}` and
# would make an environment of that name unreachable through the UI.
RESERVED_ENVIRONMENT_NAMES = frozenset({"new", "stream"})

# The value run_oqtopus_subcommand_output returns for FileNotFoundError,
# i.e. "oqtopus" isn't on PATH.
_CLI_NOT_FOUND_RETURNCODE = 127


def raise_for_command_result(result: CommandResult) -> None:
    """Raise the matching ServiceError if *result* did not succeed.

    Raises:
        CliTimeoutError: If the command was killed for exceeding its timeout.
        CliNotFoundError: If the oqtopus executable was not found (rc 127).
        CommandFailedError: If the command exited with any other non-zero code.

    """
    if result.timed_out:
        raise CliTimeoutError(result.stderr)
    if result.ok:
        return
    if result.returncode == _CLI_NOT_FOUND_RETURNCODE:
        raise CliNotFoundError
    msg = result.stderr.strip() or "oqtopus command failed"
    raise CommandFailedError(msg, returncode=result.returncode)


def check_reserved_environment_names(cfg: AppConfig) -> list[str]:
    """Return names of already-registered environments that are now reserved.

    Intended for a startup scan: these environments exist but their detail
    page is unreachable because a fixed route (e.g. ``/backend/new``)
    shadows ``/{template}/{name}``.

    Returns:
        Sorted list of colliding environment names (possibly empty).

    """
    return sorted({
        e.name for e in cfg.load_environments() if e.name in RESERVED_ENVIRONMENT_NAMES
    })


def get_environment_or_404(name: str, cfg: AppConfig) -> Environment:
    """Return the named environment.

    Returns:
        The matching Environment.

    Raises:
        EnvironmentNotFoundError: If no environment with this name exists.

    """
    env = next((e for e in cfg.load_environments() if e.name == name), None)
    if env is None:
        raise EnvironmentNotFoundError(name)
    return env


async def has_running_services(
    subcommand: str,
    root_dir: pathlib.Path,
    timeout: float,  # noqa: ASYNC109
) -> bool:
    """Return True if ``oqtopus <subcommand> status`` reports any running service.

    Fails closed: if the status check itself cannot be completed, the state
    is unknown, so this reports "running" rather than "no services running"
    so callers (e.g. environment deletion) don't treat a broken status check
    as permission to proceed.

    Returns:
        True if at least one service line reports a running state, or if the
        status check failed.

    """
    result = await run_oqtopus_subcommand_output(
        subcommand, ["status"], root_dir, timeout
    )
    if not result.ok:
        logger.warning(
            "oqtopus %s status failed (returncode=%s): %s",
            subcommand,
            result.returncode,
            result.stderr.strip(),
        )
        return True
    return any(service.running for service in parse_service_status(result))


def validate_new_environment(
    cfg: AppConfig, name: str, template: str, root_path: str
) -> None:
    """Validate a new environment request against existing environments.

    Does not persist anything; persistence happens once the init stream
    (:func:`stream_environment_init`) completes successfully.

    Raises:
        EnvironmentAlreadyExistsError: If an environment with this name exists.
        EnvironmentValidationError: If the name/template/root_path is invalid.
        ReservedEnvironmentNameError: If the name collides with a fixed route
            segment (see RESERVED_ENVIRONMENT_NAMES).

    """
    if name in RESERVED_ENVIRONMENT_NAMES:
        raise ReservedEnvironmentNameError(name)
    if any(e.name == name for e in cfg.load_environments()):
        raise EnvironmentAlreadyExistsError(name)
    try:
        Environment(
            name=name,
            template=template,
            root_path=pathlib.Path(root_path) if root_path.strip() else None,
        )
    except ValidationError as exc:
        raise EnvironmentValidationError(exc.errors()[0]["msg"]) from exc


async def stream_environment_init(
    cfg: AppConfig, name: str, template: str, root_path: str
) -> AsyncGenerator[str]:
    """Run ``oqtopus init`` and stream its output, saving the environment on success.

    Yields:
        Server-Sent Events-formatted strings for streaming to the client.

    """
    new_env = Environment(
        name=name,
        template=template,
        root_path=pathlib.Path(root_path) if root_path.strip() else None,
    )
    parent_dir = new_env.resolved_root_path(cfg.default_environment_base_path).parent
    parent_dir.mkdir(parents=True, exist_ok=True)

    success = False
    async for chunk in stream_oqtopus_init(
        name=name, template=template, cwd=parent_dir
    ):
        yield chunk
        if "event: done\ndata: success" in chunk:
            success = True

    if success:
        environments = cfg.load_environments()
        # Guard against duplicate entries if config was modified concurrently
        if not any(e.name == name for e in environments):
            resolved = new_env.resolved_root_path(cfg.default_environment_base_path)
            # Persist absolute path so the entry is cwd-independent
            env_to_save = new_env.model_copy(update={"root_path": resolved})
            environments.append(env_to_save)
            cfg.save_environments(environments)
            logger.info("Environment '%s' created and saved to config", name)


async def delete_environment(cfg: AppConfig, name: str, subcommand: str) -> None:
    """Delete an environment's directory and remove it from the config.

    Raises:
        EnvironmentNotFoundError: If no environment with this name exists.
        ServicesStillRunningError: If any service is still running.

    """
    environments = cfg.load_environments()
    target = next((e for e in environments if e.name == name), None)
    if target is None:
        raise EnvironmentNotFoundError(name)

    root_dir = target.resolved_root_path(cfg.default_environment_base_path)
    if root_dir.exists() and await has_running_services(
        subcommand, root_dir, cfg.oqtopus_cli_timeout_sec
    ):
        raise ServicesStillRunningError(name)

    logger.info("Deleting environment '%s' (root=%s)", name, root_dir)
    if root_dir.exists():
        shutil.rmtree(root_dir)
        logger.info("Deleted directory: %s", root_dir)

    remaining = [e for e in environments if e.name != name]
    cfg.save_environments(remaining)


# ── File lock / edit / save ─────────────────────────────────────────────────


@dataclass(frozen=True)
class LockState:
    """Current state of a lock file."""

    is_locked: bool
    token: str | None
    locked_since: str | None
    locked_since_ts: float | None


@dataclass(frozen=True)
class LockAcquired:
    """A newly acquired lock."""

    token: str
    acquired_ts: float


def _parse_lock_file(
    lock_path: pathlib.Path, timeout: int
) -> tuple[str, str, float] | None:
    """Parse a lock file and return (token, locked_since, locked_since_ts) if active.

    Returns:
        Lock info tuple if still active, or None if stale.

    """
    parts = lock_path.read_text(encoding="utf-8").strip().split("\n", 1)
    token = parts[0]
    # Default to epoch 0 so a missing timestamp is always treated as stale
    ts = float(parts[1]) if len(parts) > 1 else 0.0
    if time.time() - ts >= timeout:
        return None
    locked_since = datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return token, locked_since, ts


def check_lock(lock_path: pathlib.Path, timeout: int) -> LockState:
    """Check whether *lock_path* holds an active lock.

    Removes stale or corrupted lock files automatically.

    Returns:
        The current LockState.

    """
    unlocked = LockState(
        is_locked=False, token=None, locked_since=None, locked_since_ts=None
    )
    if not lock_path.exists():
        return unlocked
    try:
        parsed = _parse_lock_file(lock_path, timeout)
    except ValueError, OSError:
        # Treat corrupted lock files the same as stale ones
        parsed = None
    if parsed is None:
        lock_path.unlink(missing_ok=True)
        return unlocked
    token, locked_since, locked_since_ts = parsed
    return LockState(
        is_locked=True,
        token=token,
        locked_since=locked_since,
        locked_since_ts=locked_since_ts,
    )


def force_unlock_file(lock_path: pathlib.Path) -> None:
    """Remove a lock file unconditionally."""
    lock_path.unlink(missing_ok=True)
    logger.warning("Force-unlocked: %s", lock_path)


def acquire_file_lock(lock_path: pathlib.Path, timeout: int) -> LockAcquired:
    """Acquire a lock on the given lock file path.

    Returns:
        The newly acquired lock's token and timestamp.

    Raises:
        LockConflictError: If the file is already locked.

    """
    state = check_lock(lock_path, timeout)
    if state.is_locked:
        raise LockConflictError(state.locked_since, state.locked_since_ts)
    ts = time.time()
    token = str(uuid.uuid4())  # 128-bit random; uuid4 avoids MAC-address/time leakage
    lock_path.write_text(f"{token}\n{ts}", encoding="utf-8")
    return LockAcquired(token=token, acquired_ts=ts)


def release_file_lock(lock_path: pathlib.Path, token: str, timeout: int) -> None:
    """Release a lock if the token matches.

    Raises:
        LockNotHeldError: If the file isn't locked, or the token doesn't match.

    """
    state = check_lock(lock_path, timeout)
    if not (state.is_locked and state.token == token):
        raise LockNotHeldError
    lock_path.unlink(missing_ok=True)


def save_file(
    file_path: pathlib.Path,
    lock_path: pathlib.Path,
    content: str,
    token: str,
    timeout: int,
) -> None:
    """Validate lock token, back up the file, write new content, and release lock.

    Raises:
        LockExpiredError: If the lock has expired.
        LockTokenMismatchError: If the token doesn't match the held lock.

    """
    state = check_lock(lock_path, timeout)
    if not state.is_locked:
        raise LockExpiredError
    if state.token != token:
        raise LockTokenMismatchError
    if file_path.exists():
        # Timestamped backup so every save is recoverable without external VCS
        backup_ts = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d%H%M%S")
        backup_path = file_path.parent / f"{file_path.name}.{backup_ts}"
        shutil.copy2(file_path, backup_path)
        logger.debug("Backup created: %s", backup_path)
    file_path.write_text(content, encoding="utf-8")
    lock_path.unlink(missing_ok=True)
    logger.info("Saved: %s", file_path)


async def fetch_dotenv_template(raw_url: str) -> str | None:
    """Fetch the upstream .env template from GitHub.

    Returns:
        Template content, or None on network/HTTP failure.

    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(raw_url)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError, httpx.TimeoutException:
        return None


def resolve_existing_log_file(
    get_log_file: Callable[[pathlib.Path, str], pathlib.Path | None],
    env_root: pathlib.Path,
    service: str,
) -> pathlib.Path:
    """Resolve a service's log file path, requiring it to exist.

    ``get_log_file`` differs per template type (backend reads from YAML,
    cloud-local uses a fixed path).

    Returns:
        The existing log file Path.

    Raises:
        LogFileNotFoundError: If the log file cannot be resolved or is missing.

    """
    log_file = get_log_file(env_root, service)
    if log_file is None or not log_file.exists():
        msg = "Log file not found."
        raise LogFileNotFoundError(msg)
    return log_file


# ── GET /api/{template}?include_status aggregation ──────────────────────────


@dataclass(frozen=True)
class Outcome:
    """Either a successful result or the ServiceError raised fetching it."""

    data: BaseModel | None
    error: ServiceError | None


async def _fetch_outcome(coro: Awaitable[BaseModel]) -> Outcome:
    try:
        result = await coro
    except ServiceError as exc:
        return Outcome(data=None, error=exc)
    return Outcome(data=result, error=None)


async def build_environment_list(  # noqa: PLR0913
    cfg: AppConfig,
    environments: list[Environment],
    template: str,
    *,
    include_status: bool,
    has_device_status: bool,
    get_info: Callable[[AppConfig, str], Awaitable[BaseModel]],
    get_status: Callable[[AppConfig, str], Awaitable[BaseModel]],
    get_device_status: Callable[[AppConfig, str], Awaitable[BaseModel]] | None,
) -> dict:
    """Fetch info (and optionally status/device-status) for every environment.

    Per-environment failures are captured as an outcome rather than aborting
    the whole request: partial failure is a 200, not an all-or-nothing
    error. ``status``/``device-status`` are only fetched for environments
    where ``info`` succeeded and reports ``all_installed`` (preserving the
    list page's previous ``all_installed``-gated fetch behavior).

    Concurrency is capped at ``_LIST_FETCH_CONCURRENCY`` across the whole
    call, not per-environment, since a single environment can issue up to
    three subprocesses (info + status + device-status).

    Returns:
        Dict with keys ``template`` and ``environments`` (each entry has
        ``name``, ``environment``, ``status``, and -- for templates with
        device status -- ``device_status``, as ``Outcome`` instances for
        the router layer to serialize).

    """
    sem = asyncio.Semaphore(_LIST_FETCH_CONCURRENCY)

    async def _limited(coro: Awaitable[BaseModel]) -> Outcome:
        async with sem:
            return await _fetch_outcome(coro)

    async def _one(env: Environment) -> dict:
        env_outcome = await _limited(get_info(cfg, env.name))
        entry: dict = {"name": env.name, "environment": env_outcome}

        status_outcome: Outcome | None = None
        device_outcome: Outcome | None = None
        if (
            include_status
            and env_outcome.data is not None
            and getattr(env_outcome.data, "all_installed", False)
        ):
            status_outcome = await _limited(get_status(cfg, env.name))
            if has_device_status and get_device_status is not None:
                device_outcome = await _limited(get_device_status(cfg, env.name))

        entry["status"] = status_outcome
        if has_device_status:
            entry["device_status"] = device_outcome
        return entry

    entries = await asyncio.gather(*(_one(env) for env in environments))
    return {"template": template, "environments": list(entries)}
