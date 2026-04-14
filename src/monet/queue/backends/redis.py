"""Redis-backed task queue with pub/sub completion notifications.

Compatible with standard Redis and Upstash Redis. Supports both
pub/sub notification mode and polling fallback for environments
where pub/sub is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from monet._constants import STANDARD_REDIS_PORT
from monet.core._serialization import (
    deserialize_result,
    now_iso,
    safe_parse_context,
    serialize_result,
)
from monet.queue import TaskStatus
from monet.signals import SignalType
from monet.types import AgentResult, Signal

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from monet.queue import TaskRecord
    from monet.types import AgentRunContext

__all__ = ["RedisTaskQueue"]

_log = logging.getLogger(__name__)

# Default lease TTL in seconds.
_DEFAULT_LEASE_TTL = 300

# How often the sweeper checks for expired leases.
_SWEEPER_INTERVAL = 30.0

# Polling interval for fallback mode.
_POLL_INTERVAL = 0.5

_DEFAULT_URL = f"redis://localhost:{STANDARD_REDIS_PORT}"


def _normalize_url(url: str) -> str:
    """Convert Upstash-style https:// URLs to rediss:// for redis-py."""
    if url.startswith("https://"):
        return "rediss://" + url[len("https://") :]
    return url


class RedisTaskQueue:
    """Persistent task queue backed by Redis.

    Supports lease-based claiming for crash recovery: claimed tasks
    whose leases expire are automatically requeued by a background
    sweeper task.

    Compatible with standard Redis and Upstash Redis (TLS via
    ``rediss://`` or ``https://`` URLs).

    Args:
        url: Redis connection URL. Accepts ``redis://``, ``rediss://``,
            and ``https://`` (Upstash) schemes.
        lease_ttl: Seconds before a claimed task's lease expires and
            the sweeper requeues it.
        prefix: Key prefix for all Redis keys. Allows multiple
            independent queues in the same Redis instance.
        use_polling: If True, poll_result uses interval-based polling
            instead of pub/sub. Required for some serverless Redis
            providers that don't support pub/sub.
    """

    def __init__(
        self,
        url: str = _DEFAULT_URL,
        *,
        lease_ttl: int = _DEFAULT_LEASE_TTL,
        prefix: str = "monet",
        use_polling: bool = False,
    ) -> None:
        self._url = _normalize_url(url)
        self._lease_ttl = lease_ttl
        self._prefix = prefix
        self._use_polling = use_polling
        self._client: aioredis.Redis | None = None
        self._sweeper_task: asyncio.Task[None] | None = None

    def _task_key(self, task_id: str) -> str:
        return f"{self._prefix}:task:{task_id}"

    def _queue_key(self, pool: str) -> str:
        return f"{self._prefix}:queue:{pool}"

    def _result_channel(self, task_id: str) -> str:
        return f"{self._prefix}:result:{task_id}"

    async def _ensure_client(self) -> aioredis.Redis:
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(self._url)
        return self._client

    async def close(self) -> None:
        """Close the Redis connection and stop the sweeper."""
        self.stop_sweeper()
        if self._client is not None:
            await self._client.close()
            self._client = None

    # --- Producer API ---

    async def enqueue(
        self,
        agent_id: str,
        command: str,
        ctx: AgentRunContext,
        pool: str = "local",
    ) -> str:
        """Submit a task to the queue.

        Args:
            agent_id: Target agent identifier.
            command: Agent command to invoke.
            ctx: Full agent run context.
            pool: Pool this task belongs to.

        Returns:
            task_id that can be passed to ``poll_result``.
        """
        client = await self._ensure_client()
        task_id = str(uuid.uuid4())
        now = now_iso()

        pipe = client.pipeline()
        pipe.hset(
            self._task_key(task_id),
            mapping={
                "task_id": task_id,
                "agent_id": agent_id,
                "command": command,
                "pool": pool,
                "context": json.dumps(ctx),
                "status": TaskStatus.PENDING,
                "created_at": now,
            },
        )
        pipe.lpush(self._queue_key(pool), task_id)
        await pipe.execute()
        return task_id

    async def poll_result(self, task_id: str, timeout: float) -> AgentResult:
        """Block until the task is completed or failed.

        Uses pub/sub notifications by default. Falls back to interval
        polling when ``use_polling=True`` was set in the constructor.

        Raises:
            TimeoutError: if ``timeout`` seconds elapse without a result.
            KeyError: if ``task_id`` is unknown.
        """
        client = await self._ensure_client()

        # Verify task exists.
        status = await client.hget(self._task_key(task_id), "status")
        if status is None:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)

        status_str = status.decode() if isinstance(status, bytes) else status

        # If already terminal, return immediately.
        if status_str in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            return await self._read_result(client, task_id)

        if self._use_polling:
            return await self._poll_result_polling(client, task_id, timeout)
        return await self._poll_result_pubsub(client, task_id, timeout)

    async def _poll_result_pubsub(
        self,
        client: aioredis.Redis,
        task_id: str,
        timeout: float,
    ) -> AgentResult:
        """Wait for completion via pub/sub notification."""
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(self._result_channel(task_id))

            # Check status again after subscribing to avoid race condition
            # where completion happened between the first check and subscribe.
            status = await client.hget(self._task_key(task_id), "status")
            status_str = status.decode() if isinstance(status, bytes) else status
            if status_str in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                return await self._read_result(client, task_id)

            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    msg = f"Task {task_id} did not complete within {timeout}s"
                    raise TimeoutError(msg)
                msg_data = await asyncio.wait_for(
                    pubsub.get_message(  # type: ignore[arg-type]
                        ignore_subscribe_messages=True, timeout=remaining
                    ),
                    timeout=remaining,
                )
                if msg_data is not None:
                    return await self._read_result(client, task_id)
        finally:
            await pubsub.unsubscribe(self._result_channel(task_id))
            await pubsub.close()

    async def _poll_result_polling(
        self,
        client: aioredis.Redis,
        task_id: str,
        timeout: float,
    ) -> AgentResult:
        """Wait for completion via interval-based polling."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            status = await client.hget(self._task_key(task_id), "status")
            status_str = status.decode() if isinstance(status, bytes) else status
            if status_str in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                return await self._read_result(client, task_id)

            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                msg = f"Task {task_id} did not complete within {timeout}s"
                raise TimeoutError(msg)

            await asyncio.sleep(min(_POLL_INTERVAL, remaining))

    async def _read_result(self, client: aioredis.Redis, task_id: str) -> AgentResult:
        """Read and return the result for a terminal task."""
        data = await client.hmget(
            self._task_key(task_id), "result", "context", "status"
        )
        result_raw, ctx_raw, _status = data

        if result_raw:
            raw = result_raw.decode() if isinstance(result_raw, bytes) else result_raw
            try:
                return deserialize_result(raw)
            except (json.JSONDecodeError, KeyError):
                _log.warning("Corrupt result JSON for task %s", task_id)

        # Task failed/cancelled without a result object.
        ctx_str = ctx_raw.decode() if isinstance(ctx_raw, bytes) else ctx_raw
        ctx = safe_parse_context(ctx_str, source="redis._read_result")
        return AgentResult(
            success=False,
            output="",
            signals=(
                Signal(
                    type=SignalType.SEMANTIC_ERROR,
                    reason="Task failed in queue",
                    metadata=None,
                ),
            ),
            trace_id=ctx["trace_id"] if ctx else "",
            run_id=ctx["run_id"] if ctx else "",
        )

    # --- Consumer API ---

    async def claim(self, pool: str) -> TaskRecord | None:
        """Claim the next pending task in the given pool.

        Uses RPOP for FIFO ordering (LPUSH + RPOP). Non-blocking:
        returns None if the pool's queue is empty.

        Returns:
            A TaskRecord with status CLAIMED, or None if nothing available.
        """
        client = await self._ensure_client()
        raw_id = await client.rpop(self._queue_key(pool))
        if raw_id is None:
            return None

        task_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
        now = now_iso()
        lease_expires = datetime.now(UTC).timestamp() + self._lease_ttl
        lease_expires_iso = datetime.fromtimestamp(lease_expires, tz=UTC).isoformat()

        await client.hset(
            self._task_key(task_id),
            mapping={
                "status": TaskStatus.CLAIMED,
                "claimed_at": now,
                "lease_expires_at": lease_expires_iso,
            },
        )

        return await self._build_record(client, task_id)

    async def complete(self, task_id: str, result: AgentResult) -> None:
        """Post a successful result for a claimed task."""
        client = await self._ensure_client()
        now = now_iso()

        exists = await client.exists(self._task_key(task_id))
        if not exists:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)

        pipe = client.pipeline()
        pipe.hset(
            self._task_key(task_id),
            mapping={
                "status": TaskStatus.COMPLETED,
                "result": serialize_result(result),
                "completed_at": now,
            },
        )
        pipe.publish(self._result_channel(task_id), "done")
        await pipe.execute()

    async def fail(self, task_id: str, error: str) -> None:
        """Post a failure for a claimed task."""
        client = await self._ensure_client()

        ctx_raw = await client.hget(self._task_key(task_id), "context")
        if ctx_raw is None:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)

        ctx_str = ctx_raw.decode() if isinstance(ctx_raw, bytes) else ctx_raw
        ctx = safe_parse_context(ctx_str, source="redis.fail")
        fail_result = AgentResult(
            success=False,
            output="",
            signals=(
                Signal(
                    type=SignalType.SEMANTIC_ERROR,
                    reason=error,
                    metadata=None,
                ),
            ),
            trace_id=ctx["trace_id"] if ctx else "",
            run_id=ctx["run_id"] if ctx else "",
        )

        now = now_iso()
        pipe = client.pipeline()
        pipe.hset(
            self._task_key(task_id),
            mapping={
                "status": TaskStatus.FAILED,
                "result": serialize_result(fail_result),
                "completed_at": now,
            },
        )
        pipe.publish(self._result_channel(task_id), "done")
        await pipe.execute()

    async def cancel(self, task_id: str) -> None:
        """Cancel a pending or claimed task.

        Sets status to CANCELLED and publishes a completion notification
        so poll_result unblocks. If already completed/failed/cancelled,
        this is a no-op.
        """
        client = await self._ensure_client()

        status_raw = await client.hget(self._task_key(task_id), "status")
        if status_raw is None:
            return

        status_str = (
            status_raw.decode() if isinstance(status_raw, bytes) else status_raw
        )
        if status_str in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            return

        now = now_iso()

        # Remove from queue list if still pending.
        if status_str == TaskStatus.PENDING:
            pool_raw = await client.hget(self._task_key(task_id), "pool")
            if pool_raw is not None:
                pool = pool_raw.decode() if isinstance(pool_raw, bytes) else pool_raw
                await client.lrem(self._queue_key(pool), 0, task_id)

        pipe = client.pipeline()
        pipe.hset(
            self._task_key(task_id),
            mapping={
                "status": TaskStatus.CANCELLED,
                "completed_at": now,
            },
        )
        pipe.publish(self._result_channel(task_id), "done")
        await pipe.execute()

    # --- Lease sweeper ---

    def start_sweeper(self) -> None:
        """Start the background lease-expiry sweeper task."""
        if self._sweeper_task is not None and not self._sweeper_task.done():
            return
        self._sweeper_task = asyncio.create_task(self._sweeper_loop())

    def stop_sweeper(self) -> None:
        """Cancel the background sweeper task."""
        if self._sweeper_task is not None:
            self._sweeper_task.cancel()
            self._sweeper_task = None

    async def _sweeper_loop(self) -> None:
        """Periodically requeue tasks with expired leases."""
        while True:
            await asyncio.sleep(_SWEEPER_INTERVAL)
            await self._sweep_expired_leases()

    async def _sweep_expired_leases(self) -> None:
        """Requeue claimed tasks whose lease has expired."""
        client = await self._ensure_client()
        now = now_iso()

        cursor: int | bytes = 0
        while True:
            cursor, keys = await client.scan(
                cursor=cursor,  # type: ignore[arg-type]
                match=f"{self._prefix}:task:*",
                count=100,
            )
            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                data = await client.hmget(key_str, "status", "lease_expires_at", "pool")
                status_raw, lease_raw, pool_raw = data
                if status_raw is None:
                    continue

                status_str = (
                    status_raw.decode() if isinstance(status_raw, bytes) else status_raw
                )
                if status_str != TaskStatus.CLAIMED:
                    continue

                if lease_raw is None:
                    continue

                lease_str = (
                    lease_raw.decode() if isinstance(lease_raw, bytes) else lease_raw
                )
                if lease_str >= now:
                    continue

                # Lease expired: reset to pending and re-enqueue.
                pool_str = (
                    pool_raw.decode() if isinstance(pool_raw, bytes) else pool_raw
                )
                task_id = key_str.split(":")[-1]

                pipe = client.pipeline()
                pipe.hset(
                    key_str,
                    mapping={
                        "status": TaskStatus.PENDING,
                        "claimed_at": "",
                        "lease_expires_at": "",
                    },
                )
                pipe.lpush(self._queue_key(pool_str), task_id)
                await pipe.execute()

            # cursor == 0 means scan is complete.
            if cursor == 0 or cursor == b"0":
                break

    # --- Internal helpers ---

    async def _build_record(
        self, client: aioredis.Redis, task_id: str
    ) -> TaskRecord | None:
        """Build a TaskRecord from the Redis hash.

        Returns None if the hash is missing or contains corrupt data.
        """
        data = await client.hgetall(self._task_key(task_id))
        if not data:
            _log.warning("Empty hash for task %s", task_id)
            return None

        def _s(val: Any) -> str:
            return val.decode() if isinstance(val, bytes) else (val or "")

        result_raw = data.get(b"result") or data.get("result")
        result: AgentResult | None = None
        if result_raw:
            try:
                result = deserialize_result(_s(result_raw))
            except (json.JSONDecodeError, KeyError):
                _log.warning("Corrupt result JSON for task %s", task_id)

        ctx_raw = data.get(b"context") or data.get("context")
        ctx = safe_parse_context(_s(ctx_raw), source="redis._build_record")
        if ctx is None:
            ctx = {}

        claimed_at_raw = data.get(b"claimed_at") or data.get("claimed_at")
        claimed_at = _s(claimed_at_raw) if claimed_at_raw else None

        completed_at_raw = data.get(b"completed_at") or data.get("completed_at")
        completed_at = _s(completed_at_raw) if completed_at_raw else None

        return {  # type: ignore[return-value]
            "task_id": _s(data.get(b"task_id") or data.get("task_id")),
            "agent_id": _s(data.get(b"agent_id") or data.get("agent_id")),
            "command": _s(data.get(b"command") or data.get("command")),
            "pool": _s(data.get(b"pool") or data.get("pool")),
            "context": ctx,  # type: ignore[typeddict-item]
            "status": TaskStatus(_s(data.get(b"status") or data.get("status"))),
            "result": result,
            "created_at": _s(data.get(b"created_at") or data.get("created_at")),
            "claimed_at": claimed_at,
            "completed_at": completed_at,
        }

    # --- Progress streaming ---

    async def publish_progress(self, task_id: str, data: dict[str, Any]) -> None:
        """No-op: cross-process Redis progress requires pub/sub wiring.

        Implemented as a follow-on task. In the meantime, workers that
        need progress fan-out should use InMemoryTaskQueue (monolith) or
        POST to the server's /progress route via RemoteQueue.
        """
        return

    def subscribe_progress(self, task_id: str) -> Any:
        """Raise NotImplementedError.

        See ``publish_progress`` note — pub/sub subscription is a
        follow-on task. ``_forward_progress`` in ``invoke_agent``
        suppresses this exception.
        """
        raise NotImplementedError(
            "subscribe_progress is not yet implemented for RedisTaskQueue. "
            "Use InMemoryTaskQueue for progress streaming, or route "
            "workers through the server's POST /progress endpoint."
        )
