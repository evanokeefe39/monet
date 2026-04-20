"""Redis Streams task queue — production reference implementation.

Transport-neutral ``TaskQueue`` impl backed by Redis Streams + Pub/Sub
and result TTL strings. Works against any Redis-protocol provider
(self-hosted, Railway, Upstash TCP, ElastiCache, Memorystore) via the
``REDIS_URI`` env var.

Key shapes:

- ``work:{pool}`` stream — dispatch queue, one consumer group per pool.
- ``result:{task_id}`` string — completion payload, TTL-bound.
- ``progress:{task_id}`` pub/sub channel — ephemeral progress relay.
- ``result-ready:{task_id}`` pub/sub channel — completion notification.
- ``taskidx:{task_id}`` hash — ``{stream_id, pool}`` bookkeeping for
  XACK at complete/fail time.

Completion sequence: ``SET result:{task_id} ... EX ttl`` →
``XACK work:{pool} {pool} {stream_id}`` (pipelined) → ``PUBLISH
result-ready:{task_id} ""`` (outside the pipeline; race on the publish
is recovered by :func:`wait_completion`'s re-GET).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import time
from typing import TYPE_CHECKING, Any

from monet._ports import MAX_INLINE_PAYLOAD_BYTES
from monet.core._serialization import (
    deserialize_result,
    deserialize_task_record,
    serialize_result,
    serialize_task_record,
)
from monet.queue._interface import TaskRecord, TaskStatus
from monet.signals import SignalType
from monet.types import AgentResult, Signal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

try:
    from redis import asyncio as redis_asyncio
    from redis.exceptions import ResponseError
except ImportError as exc:  # pragma: no cover
    msg = (
        "RedisStreamsTaskQueue requires the 'redis' package. "
        "Install with: pip install 'redis>=5.0'"
    )
    raise ImportError(msg) from exc

__all__ = ["RedisStreamsTaskQueue"]

_log = logging.getLogger("monet.queue.redis_streams")

_DEFAULT_LEASE_TTL_SECONDS = 300
_DEFAULT_POOL_SIZE = 20
_DEFAULT_RESULT_TTL_MULTIPLIER = 2


def _work_key(pool: str) -> str:
    return f"work:{pool}"


def _result_key(task_id: str) -> str:
    return f"result:{task_id}"


def _ready_channel(task_id: str) -> str:
    return f"result-ready:{task_id}"


def _progress_channel(task_id: str) -> str:
    return f"progress:{task_id}"


def _index_key(task_id: str) -> str:
    return f"taskidx:{task_id}"


class RedisStreamsTaskQueue:
    """Task queue backed by Redis Streams + Pub/Sub.

    Workers do NOT use this class directly — they POST to Aegra HTTP
    endpoints which delegate here. The class is instantiated server-side
    during :func:`monet.server.bootstrap`.
    """

    def __init__(
        self,
        redis_uri: str,
        *,
        work_stream_maxlen: int | None = None,
        pool_size: int = _DEFAULT_POOL_SIZE,
        lease_ttl_seconds: int = _DEFAULT_LEASE_TTL_SECONDS,
    ) -> None:
        self._uri = redis_uri
        self._maxlen = work_stream_maxlen
        self._pool_size = pool_size
        self._lease_ttl = lease_ttl_seconds
        self._client: redis_asyncio.Redis | None = None
        self._known_pools: set[str] = set()
        self._consumer_prefix = f"sweeper-{socket.gethostname()}"

    # --- Client lifecycle -------------------------------------------------

    async def _ensure_client(self) -> redis_asyncio.Redis:
        if self._client is None:
            self._client = redis_asyncio.from_url(
                self._uri,
                max_connections=self._pool_size,
                decode_responses=True,
                socket_timeout=float(self._lease_ttl),
                socket_connect_timeout=5.0,
            )
        return self._client

    async def ping(self) -> bool:
        """Return True on a successful PING, False on any Redis error."""
        try:
            client = await self._ensure_client()
            return bool(await client.ping())  # type: ignore[misc]
        except Exception:
            _log.exception("Redis PING failed")
            return False

    @property
    def backend_name(self) -> str:
        return "redis"

    @property
    def lease_ttl_seconds(self) -> float:
        return self._lease_ttl

    async def close(self) -> None:
        """Close the Redis client and release the connection pool."""
        if self._client is not None:
            await self._client.aclose()  # type: ignore[no-untyped-call]
            self._client = None

    # --- Producer side ---------------------------------------------------

    async def enqueue(self, task: TaskRecord) -> str:
        """Submit a TaskRecord. Returns ``task["task_id"]`` unchanged.

        The Streams entry ID is stored in ``taskidx:{task_id}`` so
        ``complete`` / ``fail`` can XACK without needing to thread IDs
        through the caller. Raises ``ValueError`` if the serialised
        payload exceeds :data:`MAX_INLINE_PAYLOAD_BYTES`.
        """
        payload = serialize_task_record(task)
        if len(payload) > MAX_INLINE_PAYLOAD_BYTES:
            msg = (
                f"TaskRecord payload {len(payload)} bytes exceeds "
                f"MAX_INLINE_PAYLOAD_BYTES={MAX_INLINE_PAYLOAD_BYTES}; "
                "reference an ArtifactPointer instead of inlining."
            )
            raise ValueError(msg)
        client = await self._ensure_client()
        stream = _work_key(task["pool"])

        xadd_kwargs: dict[str, Any] = {}
        if self._maxlen is not None:
            xadd_kwargs["maxlen"] = self._maxlen
            xadd_kwargs["approximate"] = True

        stream_id = await client.xadd(stream, {"task": payload}, **xadd_kwargs)  # type: ignore[misc]
        # Pipeline HSET+EXPIRE atomically; if this fails, claim() rebinds.
        pipe = client.pipeline(transaction=True)
        pipe.hset(
            _index_key(task["task_id"]),
            mapping={"stream_id": stream_id, "pool": task["pool"]},
        )
        pipe.expire(_index_key(task["task_id"]), self._lease_ttl * 4)
        await pipe.execute()
        self._known_pools.add(task["pool"])
        return task["task_id"]

    async def await_completion(self, task_id: str, timeout: float) -> AgentResult:
        """Wait for a task result, race-free via subscribe-then-GET.

        Called by :func:`monet.orchestration._invoke.wait_completion` via
        isinstance dispatch. Subscribe is registered BEFORE the initial
        GET so a completion landing between the two is caught by the
        pub/sub notification; a completion that landed before subscribe
        is caught by the initial GET.
        """
        client = await self._ensure_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(_ready_channel(task_id))
        try:
            raw = await client.get(_result_key(task_id))
            if raw is not None:
                return deserialize_result(raw)

            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    msg = f"Task {task_id} did not complete within {timeout}s"
                    raise TimeoutError(msg)
                # Pass timeout to pubsub.get_message directly — it blocks
                # for up to ``timeout`` seconds waiting for a message.
                # Wrapping only with asyncio.wait_for is insufficient
                # because get_message() with no timeout returns None
                # immediately when nothing is queued.
                poll = min(remaining, 1.0)
                msg_obj = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=poll
                )
                if msg_obj is None:
                    # Fall back to re-GET in case PUBLISH was dropped.
                    raw = await client.get(_result_key(task_id))
                    if raw is not None:
                        return deserialize_result(raw)
                    continue
                # Re-GET. The publish may have raced with the SET in an
                # adversarial crash window; a spurious wake is safe to
                # loop on.
                raw = await client.get(_result_key(task_id))
                if raw is not None:
                    return deserialize_result(raw)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(_ready_channel(task_id))
            with contextlib.suppress(Exception):
                await pubsub.aclose()  # type: ignore[no-untyped-call]

    # --- Consumer side ---------------------------------------------------

    async def _ensure_group(self, pool: str) -> None:
        """Idempotently create the consumer group for ``pool``.

        Creating against a stream that doesn't exist yet is harmless
        when MKSTREAM is set; BUSYGROUP is swallowed.
        """
        client = await self._ensure_client()
        stream = _work_key(pool)
        try:
            # id="0" so a newly-created group sees any entries written
            # before the first claim; subsequent XREADGROUP calls use ">"
            # so each consumer only gets new messages from the group.
            await client.xgroup_create(stream, pool, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._known_pools.add(pool)

    async def claim(
        self, pool: str, consumer_id: str, block_ms: int
    ) -> TaskRecord | None:
        """XREADGROUP one entry from the pool, BLOCK up to ``block_ms``."""
        await self._ensure_group(pool)
        client = await self._ensure_client()
        stream = _work_key(pool)
        block = max(block_ms, 0)
        resp = await client.xreadgroup(
            groupname=pool,
            consumername=consumer_id,
            streams={stream: ">"},
            count=1,
            block=block,
        )
        if not resp:
            return None
        # resp = [(stream_name, [(stream_id, {"task": payload})])]
        _stream_name, entries = resp[0]
        if not entries:
            return None
        stream_id, fields = entries[0]
        payload = fields.get("task")
        if payload is None:
            _log.warning("Claimed entry with no task payload; acking to discard")
            await client.xack(stream, pool, stream_id)
            return None
        try:
            record = deserialize_task_record(payload)
        except (json.JSONDecodeError, KeyError, ValueError):
            _log.exception("Failed to deserialise task payload; acking to discard")
            await client.xack(stream, pool, stream_id)
            return None
        # Rebind the stream_id/pool mapping — on requeue via XCLAIM the
        # original taskidx might have expired.
        await client.hset(  # type: ignore[misc]
            _index_key(record["task_id"]),
            mapping={"stream_id": stream_id, "pool": pool},
        )
        await client.expire(_index_key(record["task_id"]), self._lease_ttl * 4)  # type: ignore[misc]
        record["status"] = TaskStatus.CLAIMED
        return record

    async def complete(self, task_id: str, result: AgentResult) -> None:
        """Store the result with TTL, XACK the stream entry, notify waiters.

        Idempotent — if result already stored, skips silently.
        """
        client = await self._ensure_client()
        if await client.exists(_result_key(task_id)):
            _log.debug("complete() already-completed task %s, skipping", task_id)
            return
        stream_id, pool = await self._lookup_index(task_id)
        result_ttl = self._lease_ttl * _DEFAULT_RESULT_TTL_MULTIPLIER
        pipe = client.pipeline(transaction=True)
        pipe.set(_result_key(task_id), serialize_result(result), ex=result_ttl)
        if stream_id is not None and pool is not None:
            pipe.xack(_work_key(pool), pool, stream_id)
        pipe.delete(_index_key(task_id))
        await pipe.execute()
        # Publish outside the transaction; a dropped publish is
        # recovered by wait_completion's re-GET on the next wake.
        await client.publish(_ready_channel(task_id), "")

    async def fail(self, task_id: str, error: str) -> None:
        """Record a failure AgentResult (re-uses ``complete``)."""
        result = AgentResult(
            success=False,
            output="",
            signals=(
                Signal(
                    type=SignalType.SEMANTIC_ERROR,
                    reason=error,
                    metadata=None,
                ),
            ),
        )
        await self.complete(task_id, result)

    async def _lookup_index(self, task_id: str) -> tuple[str | None, str | None]:
        client = await self._ensure_client()
        idx = await client.hgetall(_index_key(task_id))  # type: ignore[misc]
        if not idx:
            return None, None
        return idx.get("stream_id"), idx.get("pool")

    # --- Progress streaming ---------------------------------------------

    async def publish_progress(self, task_id: str, event: dict[str, Any]) -> None:
        """PUBLISH a JSON-encoded progress event. Best-effort."""
        try:
            payload = json.dumps(event)
        except (TypeError, ValueError):
            _log.debug("Progress event not JSON-serialisable, dropping", exc_info=True)
            return
        if len(payload) > MAX_INLINE_PAYLOAD_BYTES:
            _log.debug(
                "Progress event %d bytes exceeds MAX_INLINE_PAYLOAD_BYTES, dropping",
                len(payload),
            )
            return
        try:
            client = await self._ensure_client()
            await client.publish(_progress_channel(task_id), payload)
        except Exception:
            _log.debug("Progress publish failed for task %s", task_id, exc_info=True)

    async def subscribe_progress(self, task_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield progress events until the caller cancels the iterator."""
        client = await self._ensure_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(_progress_channel(task_id))
        try:
            while True:
                msg_obj = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg_obj is None:
                    continue
                data = msg_obj.get("data")
                if not isinstance(data, str):
                    continue
                try:
                    yield json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    _log.debug("Corrupt progress payload, skipping")
                    continue
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(_progress_channel(task_id))
            with contextlib.suppress(Exception):
                await pubsub.aclose()  # type: ignore[no-untyped-call]

    # --- Push dispatch tracking (restart recovery) -----------------------

    def _push_dispatch_key(self, task_id: str) -> str:
        return f"push_dispatch:{task_id}"

    async def record_push_dispatch(
        self,
        task_id: str,
        url: str,
        dispatch_secret: str | None,
        task_payload: str,
        *,
        attempt: int = 0,
    ) -> None:
        """Record an in-flight push dispatch so the server can reissue after restart."""
        client = await self._ensure_client()
        ttl = self._lease_ttl + 60
        await client.hset(  # type: ignore[misc]
            self._push_dispatch_key(task_id),
            mapping={
                "url": url,
                "dispatch_secret": dispatch_secret or "",
                "task_payload": task_payload,
                "attempt": str(attempt),
                "started_at": str(time.time()),
            },
        )
        await client.expire(self._push_dispatch_key(task_id), ttl)  # type: ignore[misc]

    async def pop_push_dispatch(self, task_id: str) -> None:
        """Remove the dispatch tracking record on success or failure."""
        client = await self._ensure_client()
        await client.delete(self._push_dispatch_key(task_id))

    async def list_in_flight_push_dispatches(
        self,
    ) -> list[dict[str, str]]:
        """Return all in-flight push dispatch records for restart recovery.

        Scans ``push_dispatch:*`` keys and returns their contents with
        ``task_id`` injected. Called once at server startup; not on the hot path.
        """
        client = await self._ensure_client()
        records: list[dict[str, str]] = []
        async for key in client.scan_iter("push_dispatch:*"):
            data: dict[str, str] = await client.hgetall(key)  # type: ignore[misc]
            if data:
                task_id = key.removeprefix("push_dispatch:")
                records.append({"task_id": task_id, **data})
        return records

    # --- QueueMaintenance protocol -----------------------------------------

    async def reclaim_expired(self) -> list[str]:
        """Protocol-compliant reclaim. Delegates to reclaim_expired_internal."""
        return await self.reclaim_expired_internal()

    # --- Sweeper (crash recovery via XPENDING / XCLAIM) ------------------

    async def reclaim_expired_internal(self) -> list[str]:
        """Reclaim PEL entries idle longer than ``lease_ttl_seconds``.

        Not on the ``TaskQueue`` protocol — impl-private helper invoked
        by the server-side sweeper task started from ``bootstrap``.
        Returns the list of stream IDs successfully XCLAIMed so callers
        can log.
        """
        client = await self._ensure_client()
        min_idle_ms = self._lease_ttl * 1000
        reclaimed: list[str] = []
        for pool in tuple(self._known_pools):
            stream = _work_key(pool)
            try:
                pending = await client.xpending_range(
                    stream, pool, min="-", max="+", count=100, idle=min_idle_ms
                )
            except ResponseError:
                # Consumer group missing — stream was trimmed or never
                # initialised. Skip until the next enqueue recreates it.
                continue
            if not pending:
                continue
            ids = [p["message_id"] for p in pending]
            try:
                claimed = await client.xclaim(
                    stream,
                    pool,
                    self._consumer_prefix,
                    min_idle_time=min_idle_ms,
                    message_ids=ids,
                )
            except ResponseError:
                _log.exception("XCLAIM failed for pool %s", pool)
                continue
            for entry in claimed:
                if isinstance(entry, tuple) and entry:
                    reclaimed.append(entry[0])
                else:
                    reclaimed.append(str(entry))
        if reclaimed:
            _log.info("Reclaimed %d expired stream entries", len(reclaimed))
        return reclaimed
