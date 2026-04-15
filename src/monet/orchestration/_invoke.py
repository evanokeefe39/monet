"""Transport-agnostic agent invocation.

Dispatches via the configured task queue. Transport (local execution,
HTTP forwarding, etc.) is a worker concern — ``invoke_agent`` only
knows about the queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from opentelemetry import trace

from monet.config import AuthConfig, OrchestrationConfig, QueueConfig
from monet.queue import TaskStatus
from monet.queue.backends.memory import InMemoryTaskQueue
from monet.queue.backends.redis_streams import RedisStreamsTaskQueue
from monet.server._auth import task_hmac

if TYPE_CHECKING:
    from monet.queue import TaskQueue, TaskRecord
    from monet.server._config import PoolConfig
    from monet.types import AgentResult, AgentRunContext

_log = logging.getLogger("monet.orchestration")

_RESERVED_FIELDS = {"task", "context", "command", "trace_id", "run_id", "skills"}

# Module-level queue — set via configure_queue() or bootstrap().
_task_queue: TaskQueue | None = None

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


def _generate_trace_id() -> str:
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


async def wait_completion(
    queue: TaskQueue, task_id: str, timeout: float
) -> AgentResult:
    """Wait for a task's completion via a backend-appropriate channel.

    Not part of the ``TaskQueue`` protocol — dispatches by concrete type
    so the protocol stays minimal. Memory uses an asyncio.Event. The
    Redis Streams branch lands in Phase 2 of the queue refactor.

    Raises:
        TimeoutError: if ``timeout`` seconds elapse without a result.
        TypeError: if the queue backend does not support completion waits.
    """
    if isinstance(queue, InMemoryTaskQueue | RedisStreamsTaskQueue):
        return await queue._await_completion(task_id, timeout)
    msg = f"wait_completion not supported for {type(queue).__name__}"
    raise TypeError(msg)


async def invoke_agent(
    agent_id: str,
    command: str = "fast",
    task: str = "",
    context: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    skills: list[str] | None = None,
    **kwargs: Any,
) -> AgentResult:
    """Invoke an agent by ID and command via the task queue.

    Standard envelope fields are explicit parameters. Agent-specific
    parameters pass as **kwargs but must not shadow reserved fields.
    Routing is always driven by AgentResult.signals, never by kwargs values.
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

    # Pool routing via the agent manifest handle.
    from monet.core.agent_manifest import get_agent_manifest

    manifest = get_agent_manifest()
    pool = manifest.get_pool(agent_id, command)
    if pool is None:
        if manifest.is_configured():
            msg = (
                f"Agent '{agent_id}/{command}' not found in manifest. "
                "Cannot determine pool."
            )
            raise ValueError(msg)
        pool = "local"

    task_id = str(uuid.uuid4())
    record: TaskRecord = {
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
    with tracer.start_as_current_span(
        f"agent.{agent_id}.{command}",
        attributes={
            "agent.id": agent_id,
            "agent.command": command,
            "monet.run_id": resolved_run_id,
        },
    ) as span:
        # Register the task with the queue so wait_completion has state
        # to observe. Push pools additionally POST to the provider
        # webhook — pull pools rely on a worker's claim loop.
        await _task_queue.enqueue(record)
        pool_cfg = _load_push_pool(pool)
        if pool_cfg is not None:
            await _dispatch_push(task_id, record, pool_cfg)
            span.set_attribute("agent.pool.type", "push")
        # Forward worker-side progress into the current LangGraph stream
        # via emit_progress. Runs concurrently with wait_completion.
        progress_task = asyncio.create_task(_forward_progress(_task_queue, task_id))
        try:
            timeout = OrchestrationConfig.load().agent_timeout
            result = await wait_completion(_task_queue, task_id, timeout=timeout)
            span.set_attribute("agent.success", result.success)
            span.set_attribute("agent.signal_count", len(result.signals))
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
    from monet.server._config import load_config

    try:
        pools = load_config()
    except (ValueError, FileNotFoundError):
        return None
    cfg = pools.get(pool)
    if cfg is None or cfg.type != "push":
        return None
    return cfg


async def _dispatch_push(
    task_id: str, record: TaskRecord, pool_cfg: PoolConfig
) -> None:
    """POST the dispatch envelope to a push-pool webhook."""
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
    envelope = {
        "task_id": task_id,
        "token": token,
        "callback_url": f"{api_url.rstrip('/')}/api/v1/tasks/{task_id}",
        "payload": serialize_task_record(record),
    }
    headers = {}
    if pool_cfg.dispatch_secret:
        headers["Authorization"] = f"Bearer {pool_cfg.dispatch_secret}"
    client = await get_dispatch_client()
    resp = await client.post(pool_cfg.url, json=envelope, headers=headers)
    if resp.status_code >= 400:
        msg = (
            f"Push dispatch to {pool_cfg.url} for task {task_id} returned "
            f"HTTP {resp.status_code}: {resp.text[:200]}"
        )
        raise RuntimeError(msg)


async def _forward_progress(queue: TaskQueue, task_id: str) -> None:
    """Forward queue progress events to the current LangGraph stream."""
    from monet.core.stubs import emit_progress

    try:
        async for event in queue.subscribe_progress(task_id):
            emit_progress(event)
    except NotImplementedError:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        _log.debug("Progress forwarding ended for task %s", task_id, exc_info=True)
