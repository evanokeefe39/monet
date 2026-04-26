"""HTTP client for remote workers to communicate with the orchestration server.

A remote worker uses this client to register capabilities, send
heartbeats, claim tasks, and post results back to the server. The
client wraps the server's REST API (``/api/v1/...``) endpoints.

The :class:`RemoteQueue` adapter implements the :class:`~monet.queue.TaskQueue`
protocol so ``run_worker()`` can use it transparently — the worker code
is identical whether dispatching locally or remotely.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from opentelemetry import propagate as _propagate

from monet.worker._retry import retry_with_backoff

if TYPE_CHECKING:
    from monet.events import TaskRecord
    from monet.server._capabilities import Capability
    from monet.types import AgentResult

__all__ = ["RemoteQueue", "WorkerClient", "_trace_headers"]

_log = logging.getLogger("monet.worker._client")


def _trace_headers() -> dict[str, str]:
    """Capture current OTel context as W3C HTTP propagation headers."""
    carrier: dict[str, str] = {}
    _propagate.inject(carrier)
    return carrier


_TIMEOUT = 30.0


class WorkerClient:
    """HTTP client for the monet orchestration server API."""

    def __init__(self, server_url: str, api_key: str) -> None:
        base = server_url.rstrip("/")
        self._base = f"{base}/api/v1"
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_TIMEOUT,
        )
        self._consecutive_heartbeat_failures: int = 0

    async def register(
        self,
        pool: str,
        capabilities: list[Capability],
        worker_id: str,
    ) -> str:
        """Register by sending the first heartbeat. Returns ``worker_id``.

        Registration and liveness ping share the unified endpoint
        ``POST /workers/{worker_id}/heartbeat``. Retries with exponential
        backoff on transient connection failures and 5xx responses to
        cover a server-startup race.
        """

        async def _do() -> str:
            resp = await self._client.post(
                f"/workers/{worker_id}/heartbeat",
                json={
                    "pool": pool,
                    "capabilities": [c.model_dump() for c in capabilities],
                },
            )
            resp.raise_for_status()
            return worker_id

        return await retry_with_backoff(
            _do,
            max_attempts=8,
            base_delay=1.0,
            max_delay=30.0,
            logger=_log,
        )

    async def heartbeat(
        self,
        worker_id: str,
        pool: str,
        capabilities: list[Capability] | None = None,
    ) -> None:
        """Send a heartbeat to the server.

        The unified heartbeat endpoint requires a full capability list on
        every call. When *capabilities* is ``None`` this method is a no-op
        — callers that want a pure liveness ping should omit it.
        """
        if capabilities is None:
            return
        resp = await self._client.post(
            f"/workers/{worker_id}/heartbeat",
            json={
                "pool": pool,
                "capabilities": [c.model_dump() for c in capabilities],
            },
        )
        resp.raise_for_status()

    async def heartbeat_with_tracking(
        self,
        worker_id: str,
        pool: str,
        capabilities: list[Capability] | None = None,
    ) -> None:
        """Send a heartbeat with consecutive-failure awareness.

        Swallows transient failures (connection errors, 5xx) and
        escalates log levels as consecutive failures accumulate. Hard
        auth failures (4xx) propagate so the worker crashes rather than
        logging warnings forever against a misconfigured API key.
        """
        try:
            await self.heartbeat(worker_id, pool, capabilities)
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            OSError,
        ) as exc:
            self._handle_heartbeat_failure(exc)
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                self._handle_heartbeat_failure(exc)
                return
            raise  # 4xx — auth failure or similar; let the worker die.

        if self._consecutive_heartbeat_failures > 0:
            _log.info(
                "Heartbeat recovered after %d consecutive failures",
                self._consecutive_heartbeat_failures,
            )
            self._consecutive_heartbeat_failures = 0

    def _handle_heartbeat_failure(self, exc: BaseException) -> None:
        """Record a heartbeat failure and log at an escalating level."""
        self._consecutive_heartbeat_failures += 1
        n = self._consecutive_heartbeat_failures
        if n >= 3:
            _log.error(
                "Heartbeat failed %d consecutive times (%s). "
                "Server will consider this worker stale.",
                n,
                exc,
            )
        elif n == 2:
            _log.warning(
                "Heartbeat failed %d consecutive times (%s). "
                "Server stale threshold approaching (90s).",
                n,
                exc,
            )
        else:
            _log.warning("Heartbeat failed: %s", exc)

    async def claim(
        self,
        pool: str,
        consumer_id: str,
        block_ms: int = 5000,
    ) -> TaskRecord | None:
        """Claim the next pending task. Returns None if nothing available.

        Uses ``POST /api/v1/pools/{pool}/claim`` so the server can
        validate *consumer_id* is heartbeating for *pool* (pool-scoped
        claim auth — fixes cross-pool poaching).
        """
        resp = await self._client.post(
            f"/pools/{pool}/claim",
            json={"consumer_id": consumer_id, "block_ms": block_ms},
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def complete(self, task_id: str, result: AgentResult) -> None:
        """Post a successful result for a claimed task.

        Retries on transient failures — losing a task result is worse
        than a brief delay.
        """
        headers = _trace_headers()

        async def _do() -> None:
            resp = await self._client.post(
                f"/tasks/{task_id}/complete",
                headers=headers,
                json={
                    "success": result.success,
                    "output": result.output,
                    "artifacts": [dict(a) for a in result.artifacts],
                    "signals": [dict(s) for s in result.signals],
                    "trace_id": result.trace_id,
                    "run_id": result.run_id,
                },
            )
            resp.raise_for_status()

        await retry_with_backoff(
            _do,
            max_attempts=5,
            base_delay=1.0,
            max_delay=15.0,
            logger=_log,
        )

    async def fail(self, task_id: str, error: str) -> None:
        """Post a failure for a claimed task.

        Retries on transient failures — losing error reporting hides
        real problems from operators.
        """
        headers = _trace_headers()

        async def _do() -> None:
            resp = await self._client.post(
                f"/tasks/{task_id}/fail",
                headers=headers,
                json={"error": error},
            )
            resp.raise_for_status()

        await retry_with_backoff(
            _do,
            max_attempts=5,
            base_delay=1.0,
            max_delay=15.0,
            logger=_log,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class RemoteQueue:
    """TaskQueue adapter wrapping :class:`WorkerClient`.

    Implements the consumer side of the ``TaskQueue`` protocol (claim,
    complete, fail, publish_progress) so ``run_worker()`` can use a
    remote server transparently. Producer methods raise
    ``NotImplementedError`` — enqueue is a server-side concern.
    """

    def __init__(self, client: WorkerClient, pool: str) -> None:
        self._client = client
        self._pool = pool

    async def enqueue(self, task: TaskRecord) -> str:
        raise NotImplementedError("enqueue is a server-side operation")

    async def claim(
        self, pool: str, consumer_id: str, block_ms: int
    ) -> TaskRecord | None:
        return await self._client.claim(pool, consumer_id, block_ms)

    async def complete(self, task_id: str, result: AgentResult) -> None:
        await self._client.complete(task_id, result)

    async def fail(self, task_id: str, error: str) -> None:
        await self._client.fail(task_id, error)

    async def publish_progress(self, task_id: str, event: dict[str, Any]) -> None:
        """POST a progress event to the server's progress endpoint."""
        headers = _trace_headers()
        try:
            resp = await self._client._client.post(
                f"/tasks/{task_id}/progress", headers=headers, json=event
            )
            resp.raise_for_status()
        except Exception:
            _log.debug("Failed to POST progress for task %s", task_id, exc_info=True)

    def subscribe_progress(self, task_id: str) -> Any:
        """Raise NotImplementedError — progress flows server-ward via POST."""
        raise NotImplementedError(
            "subscribe_progress is not supported on RemoteQueue. "
            "Progress flows via POST /api/v1/tasks/{task_id}/progress "
            "from the worker to the server."
        )

    async def await_completion(self, task_id: str, timeout: float) -> Any:
        """Raise NotImplementedError — completion waits are server-side."""
        raise NotImplementedError(
            "await_completion is a server-side operation; "
            "workers do not wait for task results."
        )
