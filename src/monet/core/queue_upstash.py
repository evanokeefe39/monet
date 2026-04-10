"""Upstash Redis-backed task queue — HTTP-based, serverless-friendly.

Uses the upstash-redis Python SDK (HTTP, connectionless). No pub/sub
— completion notification uses polling. Ideal for serverless and edge
deployments where persistent connections are not available.

Install: pip install upstash-redis
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from monet.queue import TaskRecord
    from monet.types import AgentResult, AgentRunContext

__all__ = ["UpstashTaskQueue"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _serialize_result(result: AgentResult) -> str:
    """Serialize an AgentResult to JSON for Redis storage."""
    return json.dumps(
        {
            "success": result.success,
            "output": result.output,
            "artifacts": [dict(a) for a in result.artifacts],
            "signals": [dict(s) for s in result.signals],
            "trace_id": result.trace_id,
            "run_id": result.run_id,
        }
    )


def _deserialize_result(data: str) -> AgentResult:
    """Deserialize an AgentResult from JSON stored in Redis."""
    from monet.types import AgentResult, ArtifactPointer, Signal

    d: dict[str, Any] = json.loads(data)
    return AgentResult(
        success=d["success"],
        output=d.get("output"),
        artifacts=tuple(
            ArtifactPointer(artifact_id=a["artifact_id"], url=a["url"])
            for a in d.get("artifacts", ())
        ),
        signals=tuple(
            Signal(type=s["type"], reason=s["reason"], metadata=s.get("metadata"))
            for s in d.get("signals", ())
        ),
        trace_id=d.get("trace_id", ""),
        run_id=d.get("run_id", ""),
    )


class UpstashTaskQueue:
    """HTTP-based task queue backed by Upstash Redis.

    Connectionless design makes this ideal for serverless environments
    (Vercel, Cloudflare Workers, AWS Lambda) where persistent TCP
    connections are not viable.

    No pub/sub — ``poll_result`` polls task status at ``poll_interval``.
    No lease sweeper — task keys auto-expire via Redis TTL. External
    lease sweeping (e.g. via QStash cron) is recommended for production.

    Args:
        url: Upstash Redis REST URL. Falls back to
            ``UPSTASH_REDIS_REST_URL`` env var.
        token: Upstash Redis REST token. Falls back to
            ``UPSTASH_REDIS_REST_TOKEN`` env var.
        prefix: Key prefix for all Redis keys.
        poll_interval: Seconds between ``poll_result`` status checks.
        task_ttl: TTL in seconds for task keys (auto-cleanup).
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        *,
        prefix: str = "monet",
        poll_interval: float = 0.5,
        task_ttl: int = 86400,
    ) -> None:
        from upstash_redis.asyncio import Redis

        if url is not None and token is not None:
            self._redis = Redis(url=url, token=token)
        else:
            # Falls back to UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN
            env_url = os.environ.get("UPSTASH_REDIS_REST_URL", "")
            env_token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
            if not env_url or not env_token:
                msg = (
                    "Upstash credentials required: pass url/token or set "
                    "UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN"
                )
                raise ValueError(msg)
            self._redis = Redis(url=env_url, token=env_token)
        self._prefix = prefix
        self._poll_interval = poll_interval
        self._task_ttl = task_ttl

    def _task_key(self, task_id: str) -> str:
        """Redis key for a task hash."""
        return f"{self._prefix}:task:{task_id}"

    def _queue_key(self, pool: str) -> str:
        """Redis key for a pool's pending-task list."""
        return f"{self._prefix}:queue:{pool}"

    # --- Producer API ---

    async def enqueue(
        self,
        agent_id: str,
        command: str,
        ctx: AgentRunContext,
        pool: str = "local",
    ) -> str:
        """Submit a task to the queue.

        Stores task fields in a Redis hash and pushes the task_id to the
        pool's list. Sets a TTL on the task key for auto-cleanup.

        Args:
            agent_id: Target agent identifier.
            command: Agent command to invoke.
            ctx: Full agent run context.
            pool: Pool this task belongs to.

        Returns:
            task_id that can be passed to ``poll_result``.
        """
        from monet.queue import TaskStatus

        task_id = str(uuid.uuid4())
        key = self._task_key(task_id)
        now = _now_iso()

        fields: dict[str, str] = {
            "task_id": task_id,
            "agent_id": agent_id,
            "command": command,
            "pool": pool,
            "context": json.dumps(ctx),
            "status": TaskStatus.PENDING,
            "created_at": now,
        }

        # HSET all fields, push to pool queue, set TTL.
        await self._redis.hset(key, values=fields)
        await self._redis.expire(key, self._task_ttl)
        await self._redis.lpush(self._queue_key(pool), task_id)
        return task_id

    async def poll_result(self, task_id: str, timeout: float) -> AgentResult:
        """Poll until the task reaches a terminal state or timeout.

        Unlike the in-memory implementation which uses asyncio.Event,
        this polls the Redis hash status field at ``poll_interval``
        because Upstash does not support pub/sub.

        Raises:
            TimeoutError: if ``timeout`` seconds elapse without a result.
            KeyError: if ``task_id`` is unknown.
        """
        from monet.queue import TaskStatus
        from monet.signals import SignalType
        from monet.types import AgentResult, Signal

        key = self._task_key(task_id)
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        elapsed = 0.0

        while True:
            status_raw = await self._redis.hget(key, "status")
            if status_raw is None:
                msg = f"Unknown task_id: {task_id}"
                raise KeyError(msg)

            if TaskStatus(status_raw) in terminal:
                result_raw = await self._redis.hget(key, "result")
                if result_raw:
                    return _deserialize_result(result_raw)
                # Terminal without a result — build a failure stub.
                ctx_raw = await self._redis.hget(key, "context")
                ctx: dict[str, Any] = json.loads(ctx_raw) if ctx_raw else {}
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
                    trace_id=ctx.get("trace_id", ""),
                    run_id=ctx.get("run_id", ""),
                )

            if elapsed >= timeout:
                msg = f"Task {task_id} did not complete within {timeout}s"
                raise TimeoutError(msg)

            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval

    # --- Consumer API ---

    async def claim(self, pool: str) -> TaskRecord | None:
        """Claim the next pending task from the pool's queue.

        Non-blocking: RPOP from the pool list, then update the task
        hash to CLAIMED status. Returns None if the queue is empty.

        Returns:
            A TaskRecord with status CLAIMED, or None if nothing available.
        """
        from monet.queue import TaskStatus

        queue_key = self._queue_key(pool)
        task_id = await self._redis.rpop(queue_key)
        if task_id is None:
            return None

        key = self._task_key(task_id)
        now = _now_iso()

        # Verify the task still exists and is pending.
        status_raw = await self._redis.hget(key, "status")
        if status_raw is None or TaskStatus(status_raw) != TaskStatus.PENDING:
            return None

        await self._redis.hset(
            key, values={"status": TaskStatus.CLAIMED, "claimed_at": now}
        )

        # Read all fields to build the record.
        data = await self._redis.hgetall(key)
        if not data:
            return None

        ctx: AgentRunContext = json.loads(data["context"])
        result_raw = data.get("result")

        record: TaskRecord = {
            "task_id": data["task_id"],
            "agent_id": data["agent_id"],
            "command": data["command"],
            "pool": data["pool"],
            "context": ctx,
            "status": TaskStatus(data["status"]),
            "result": _deserialize_result(result_raw) if result_raw else None,
            "created_at": data["created_at"],
            "claimed_at": data.get("claimed_at"),
            "completed_at": data.get("completed_at"),
        }
        return record

    async def complete(self, task_id: str, result: AgentResult) -> None:
        """Post a successful result for a claimed task.

        Raises:
            KeyError: if ``task_id`` is unknown.
        """
        from monet.queue import TaskStatus

        key = self._task_key(task_id)
        exists = await self._redis.exists(key)
        if not exists:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)

        now = _now_iso()
        await self._redis.hset(
            key,
            values={
                "status": TaskStatus.COMPLETED,
                "result": _serialize_result(result),
                "completed_at": now,
            },
        )

    async def fail(self, task_id: str, error: str) -> None:
        """Post a failure for a claimed task.

        Builds an AgentResult with a SEMANTIC_ERROR signal from the
        task's stored context, then marks the task as failed.

        Raises:
            KeyError: if ``task_id`` is unknown.
        """
        from monet.queue import TaskStatus
        from monet.signals import SignalType
        from monet.types import AgentResult, Signal

        key = self._task_key(task_id)
        ctx_raw = await self._redis.hget(key, "context")
        if ctx_raw is None:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)

        ctx: dict[str, Any] = json.loads(ctx_raw)
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
            trace_id=ctx.get("trace_id", ""),
            run_id=ctx.get("run_id", ""),
        )

        now = _now_iso()
        await self._redis.hset(
            key,
            values={
                "status": TaskStatus.FAILED,
                "result": _serialize_result(fail_result),
                "error": error,
                "completed_at": now,
            },
        )

    async def cancel(self, task_id: str) -> None:
        """Cancel a pending or claimed task.

        Updates status to CANCELLED if the task is still pending or
        claimed. If already completed, failed, or cancelled, this is
        a no-op. Also removes the task_id from its pool queue if still
        pending.
        """
        from monet.queue import TaskStatus

        key = self._task_key(task_id)
        data = await self._redis.hgetall(key)
        if not data:
            return

        status = TaskStatus(data["status"])
        if status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            return

        now = _now_iso()
        await self._redis.hset(
            key,
            values={"status": TaskStatus.CANCELLED, "completed_at": now},
        )

        # Remove from pool queue if still queued (best-effort).
        pool = data.get("pool", "local")
        await self._redis.lrem(self._queue_key(pool), 0, task_id)
