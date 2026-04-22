"""Transport-agnostic agent invocation.

Dispatches via the configured task queue. Transport (local execution,
HTTP forwarding, etc.) is a worker concern — ``invoke_agent`` only
knows about the queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import secrets
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from opentelemetry import trace

from monet.config import AuthConfig, OrchestrationConfig, QueueConfig
from monet.core.auth import task_hmac
from monet.exceptions import PushDispatchTerminal
from monet.queue import (
    TASK_RECORD_SCHEMA_VERSION,
    QueueMaintenance,
    TaskStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from monet.config import PoolConfig
    from monet.queue import TaskQueue, TaskRecord
    from monet.server._capabilities import CapabilityIndex
    from monet.types import AgentResult, AgentRunContext

_log = logging.getLogger("monet.orchestration")

_RESERVED_FIELDS = {"task", "context", "command", "trace_id", "run_id", "skills"}

# Lifecycle status conventions — colon prefix distinguishes from freeform
# agent-authored statuses (e.g. "searching with Exa").
AGENT_STARTED_STATUS = "agent:started"
AGENT_COMPLETED_STATUS = "agent:completed"
AGENT_FAILED_STATUS = "agent:failed"

# Push dispatch retry configuration. Monkeypatch in tests to skip real sleeps.
_PUSH_MAX_ATTEMPTS: int = 3
_PUSH_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 4.0, 16.0)

# Module-level queue — set via configure_queue() at server boot.
_task_queue: TaskQueue | None = None

# Module-level capability index — set via configure_capability_index() at
# server boot. Used by ``invoke_agent`` for split-fleet pool routing
# when the local registry does not own the capability.
_capability_index: CapabilityIndex | None = None

# Module-level httpx client for push-dispatch POSTs. Lazy-init on first
# use; closed by close_dispatch_client() on server shutdown.
_dispatch_client: httpx.AsyncClient | None = None


async def get_dispatch_client() -> httpx.AsyncClient:
    """Return the process-wide httpx client used for push dispatch."""
    global _dispatch_client
    if _dispatch_client is None:
        timeout = QueueConfig.load().push_dispatch_timeout
        _dispatch_client = httpx.AsyncClient(timeout=timeout)
    return _dispatch_client


async def close_dispatch_client() -> None:
    """Close the push-dispatch httpx client. Called from server shutdown."""
    global _dispatch_client
    if _dispatch_client is not None:
        await _dispatch_client.aclose()
        _dispatch_client = None


def _emit_lifecycle(data: dict[str, Any]) -> None:
    """Emit a lifecycle progress event into the current LangGraph stream."""
    from monet import emit_progress

    emit_progress(data)


def configure_queue(queue: TaskQueue | None) -> None:
    """Set or clear the task queue used by ``invoke_agent``.

    In monolith mode, ``bootstrap()`` creates an in-memory queue and
    starts a background worker. In distributed mode, the queue connects
    to an external broker (Redis, etc.).
    """
    global _task_queue
    _task_queue = queue


def get_queue() -> TaskQueue | None:
    """Return the currently configured queue, or None."""
    return _task_queue


def configure_capability_index(index: CapabilityIndex | None) -> None:
    """Set or clear the capability index used for cross-pool routing.

    Called once at server boot. Workers do not call this — they route
    in-process via the local registry's ``_pool`` metadata.
    """
    global _capability_index
    _capability_index = index


def get_capability_index() -> CapabilityIndex | None:
    """Return the currently configured capability index, or None."""
    return _capability_index


def _generate_trace_id() -> str:
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


async def wait_completion(
    queue: TaskQueue, task_id: str, timeout: float
) -> AgentResult:
    """Wait for a task's completion via the protocol method.

    Raises:
        TimeoutError: if ``timeout`` seconds elapse without a result.
        KeyError: if task_id was never enqueued.
        AwaitAlreadyConsumedError: if result TTL has expired.
    """
    return await queue.await_completion(task_id, timeout)


async def invoke_agent(
    agent_id: str,
    command: str = "fast",
    task: str = "",
    context: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    skills: list[str] | None = None,
    thread_id: str | None = None,
    **kwargs: Any,
) -> AgentResult:
    """Invoke an agent by ID and command via the task queue.

    Standard envelope fields are explicit parameters. Agent-specific
    parameters pass as **kwargs but must not shadow reserved fields.
    Routing is always driven by AgentResult.signals, never by kwargs values.

    ``thread_id`` propagates into ``AgentRunContext`` so artifacts
    written by the agent carry thread provenance — the chat TUI reads
    this via ``query_recent(thread_id=...)`` to show per-thread counts.
    """
    conflicts = _RESERVED_FIELDS & set(kwargs)
    if conflicts:
        msg = (
            f"invoke_agent() kwargs conflict with reserved fields: {conflicts}. "
            "Pass these as explicit parameters."
        )
        raise ValueError(msg)

    if _task_queue is None:
        msg = (
            "No task queue configured. "
            "Call configure_queue() or bootstrap() before invoking agents."
        )
        raise RuntimeError(msg)

    resolved_run_id = run_id or str(uuid.uuid4())
    resolved_trace_id = trace_id or _generate_trace_id()

    ctx: AgentRunContext = {
        "task": task,
        "context": context or [],
        "command": command,
        "trace_id": resolved_trace_id,
        "run_id": resolved_run_id,
        "agent_id": agent_id,
        "skills": skills or [],
    }
    if thread_id:
        ctx["thread_id"] = thread_id

    # Pool routing: prefer the local registry (in-process ``@agent``
    # handlers carry pool on the wrapper), fall back to the server
    # ``CapabilityIndex`` (populated by worker heartbeats), fall back to
    # "local" when neither knows the capability.
    from monet.core.registry import default_registry

    pool: str | None = None
    local_handler = default_registry.lookup(agent_id, command)
    if local_handler is not None:
        pool = getattr(local_handler, "_pool", None)
    if pool is None and _capability_index is not None:
        pool = _capability_index.get_pool(agent_id, command)
    if pool is None:
        pool = "local"

    task_id = str(uuid.uuid4())
    record: TaskRecord = {
        "schema_version": TASK_RECORD_SCHEMA_VERSION,
        "task_id": task_id,
        "agent_id": agent_id,
        "command": command,
        "pool": pool,
        "context": ctx,
        "status": TaskStatus.PENDING,
        "result": None,
        "created_at": datetime.now(UTC).isoformat(),
        "claimed_at": None,
        "completed_at": None,
    }

    tracer = trace.get_tracer("monet.orchestration")
    _log.info(
        "invoke_agent dispatch agent=%s command=%s pool=%s task_id=%s run_id=%s",
        agent_id,
        command,
        pool,
        task_id,
        resolved_run_id,
    )
    _lifecycle = {
        "agent": agent_id,
        "command": command,
        "run_id": resolved_run_id,
        "task_id": task_id,
    }
    _emit_lifecycle({"status": AGENT_STARTED_STATUS, **_lifecycle})
    with tracer.start_as_current_span(
        f"agent.{agent_id}.{command}",
        attributes={
            "agent.id": agent_id,
            "agent.command": command,
            "monet.run_id": resolved_run_id,
            "monet.task_id": task_id,
        },
    ) as span:
        # Register the task with the queue so wait_completion has state
        # to observe. Push pools additionally POST to the provider
        # webhook — pull pools rely on a worker's claim loop.
        await _task_queue.enqueue(record)
        pool_cfg = _load_push_pool(pool)
        if pool_cfg is not None:
            try:
                await _dispatch_push(task_id, record, pool_cfg, _task_queue)
                span.set_attribute("agent.pool.type", "push")
            except PushDispatchTerminal:
                # Failure result already written to queue by _push_with_retry;
                # fall through to wait_completion which resolves it immediately.
                span.set_attribute("agent.pool.type", "push")
                span.set_attribute("agent.dispatch_failed", True)
        _stream_writer: Callable[[dict[str, Any]], None] | None = None
        try:
            from langgraph.config import get_stream_writer

            _stream_writer = get_stream_writer()
        except (LookupError, RuntimeError):
            pass
        progress_task = asyncio.create_task(
            _forward_progress(_task_queue, task_id, writer=_stream_writer)
        )
        try:
            timeout = OrchestrationConfig.load().agent_timeout
            result = await wait_completion(_task_queue, task_id, timeout=timeout)
            span.set_attribute("agent.success", result.success)
            span.set_attribute("agent.signal_count", len(result.signals))
            _log.info(
                "invoke_agent result agent=%s command=%s success=%s "
                "signals=%d task_id=%s",
                agent_id,
                command,
                result.success,
                len(result.signals),
                task_id,
            )
            if result.success:
                _emit_lifecycle({"status": AGENT_COMPLETED_STATUS, **_lifecycle})
            else:
                reasons = []
                for s in result.signals:
                    r = s.get("reason", "")
                    if r:
                        reasons.append(r)
                _emit_lifecycle(
                    {
                        "status": AGENT_FAILED_STATUS,
                        **_lifecycle,
                        "reasons": "; ".join(reasons),
                    }
                )
            return result
        finally:
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task


def _load_push_pool(pool: str) -> PoolConfig | None:
    """Return the pool's ``PoolConfig`` when ``type="push"``, else None.

    A missing monet.toml or unconfigured pool name falls through as
    ``None`` so local/pull pools skip the push branch transparently.
    """
    from monet.config import load_pool_config as load_config

    try:
        pools = load_config()
    except (ValueError, FileNotFoundError):
        return None
    cfg = pools.get(pool)
    if cfg is None or cfg.type != "push":
        return None
    return cfg


async def _dispatch_push(
    task_id: str,
    record: TaskRecord,
    pool_cfg: PoolConfig,
    queue: TaskQueue,
) -> None:
    """POST the dispatch envelope to a push-pool webhook with retry.

    On exhaustion or terminal 4xx, writes a DISPATCH_FAILED result to the
    queue so ``wait_completion`` unblocks immediately, then raises
    :exc:`~monet.exceptions.PushDispatchTerminal`.
    """
    from monet.config import MONET_SERVER_URL
    from monet.config._env import read_str
    from monet.core._serialization import serialize_task_record

    if pool_cfg.url is None:
        msg = f"Push pool {pool_cfg.name!r} has no URL configured"
        raise RuntimeError(msg)
    api_key = AuthConfig.load().api_key
    if not api_key:
        msg = "MONET_API_KEY must be set for push-pool HMAC token derivation"
        raise RuntimeError(msg)
    api_url = read_str(MONET_SERVER_URL)
    if not api_url:
        msg = (
            "MONET_SERVER_URL must be set so push workers know where to "
            "POST progress and completion callbacks"
        )
        raise RuntimeError(msg)

    token = task_hmac(api_key, task_id)
    task_payload = serialize_task_record(record)
    envelope = {
        "task_id": task_id,
        "token": token,
        "callback_url": f"{api_url.rstrip('/')}/api/v1/tasks/{task_id}",
        "payload": task_payload,
    }
    headers: dict[str, str] = {}
    if pool_cfg.dispatch_secret:
        headers["Authorization"] = f"Bearer {pool_cfg.dispatch_secret}"

    await _push_with_retry(
        task_id,
        queue,
        pool_cfg.url,
        headers,
        envelope,
        task_payload,
        dispatch_secret=pool_cfg.dispatch_secret,
    )


async def _push_with_retry(
    task_id: str,
    queue: TaskQueue,
    url: str,
    headers: dict[str, str],
    envelope: dict[str, Any],
    task_payload: str,
    *,
    dispatch_secret: str | None = None,
) -> None:
    """POST with bounded exponential backoff.

    Retries on connection errors, read timeouts, 429, and 5xx. Other 4xx
    are treated as terminal and fail immediately. On exhaustion or terminal
    error, writes a DISPATCH_FAILED result to the queue then raises
    :exc:`~monet.exceptions.PushDispatchTerminal`.
    """
    _retryable_exceptions = (
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    )
    client = await get_dispatch_client()
    last_exc: Exception | None = None

    if isinstance(queue, QueueMaintenance):
        await queue.record_push_dispatch(
            task_id, url, dispatch_secret, task_payload, attempt=0
        )

    for attempt in range(_PUSH_MAX_ATTEMPTS):
        if isinstance(queue, QueueMaintenance) and attempt > 0:
            await queue.record_push_dispatch(
                task_id, url, dispatch_secret, task_payload, attempt=attempt
            )
        try:
            resp = await client.post(url, json=envelope, headers=headers)
        except _retryable_exceptions as exc:
            last_exc = exc
            if attempt < _PUSH_MAX_ATTEMPTS - 1:
                sleep_s = _PUSH_BACKOFF_SECONDS[attempt] * random.uniform(0.75, 1.25)
                _log.warning(
                    "push_dispatch retry %d/%d task=%s err=%s sleep=%.1fs",
                    attempt + 1,
                    _PUSH_MAX_ATTEMPTS,
                    task_id,
                    type(exc).__name__,
                    sleep_s,
                )
                await asyncio.sleep(sleep_s)
            continue

        if resp.status_code < 400:
            if isinstance(queue, QueueMaintenance):
                await queue.pop_push_dispatch(task_id)
            return

        if resp.status_code == 429 or resp.status_code >= 500:
            last_exc = RuntimeError(f"HTTP {resp.status_code}")
            if attempt < _PUSH_MAX_ATTEMPTS - 1:
                sleep_s = _PUSH_BACKOFF_SECONDS[attempt] * random.uniform(0.75, 1.25)
                _log.warning(
                    "push_dispatch retry %d/%d task=%s status=%d sleep=%.1fs",
                    attempt + 1,
                    _PUSH_MAX_ATTEMPTS,
                    task_id,
                    resp.status_code,
                    sleep_s,
                )
                await asyncio.sleep(sleep_s)
            continue

        # Terminal 4xx — fail immediately without retrying.
        detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
        _log.warning("push_dispatch terminal task_id=%s %s", task_id, detail)
        await _write_dispatch_failed(task_id, queue, detail)
        raise PushDispatchTerminal(
            f"Push dispatch terminal for task {task_id}: {detail}"
        )

    # All attempts exhausted.
    detail = f"exhausted {_PUSH_MAX_ATTEMPTS} attempts; last: {last_exc}"
    _log.warning("push_dispatch exhausted task_id=%s %s", task_id, detail)
    await _write_dispatch_failed(task_id, queue, detail)
    raise PushDispatchTerminal(f"Push dispatch failed for task {task_id}: {detail}")


async def _write_dispatch_failed(task_id: str, queue: TaskQueue, detail: str) -> None:
    """Write a DISPATCH_FAILED result to the queue and clean up the tracking record."""
    from monet.signals import SignalType
    from monet.types import AgentResult, Signal

    result = AgentResult(
        success=False,
        output="",
        signals=(
            Signal(
                type=SignalType.DISPATCH_FAILED,
                reason=f"dispatch_failed: {detail}",
                metadata=None,
            ),
        ),
    )
    await queue.complete(task_id, result)
    if isinstance(queue, QueueMaintenance):
        await queue.pop_push_dispatch(task_id)


async def _forward_progress(
    queue: TaskQueue,
    task_id: str,
    *,
    writer: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Forward queue progress events to the LangGraph stream writer."""
    if writer is None:
        return
    try:
        async for event in queue.subscribe_progress(task_id):
            writer(event)
    except NotImplementedError:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        _log.debug("Progress forwarding ended for task %s", task_id, exc_info=True)
