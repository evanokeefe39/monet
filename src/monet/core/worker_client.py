"""HTTP client for remote workers to communicate with the orchestration server.

A remote worker uses this client to register capabilities, send
heartbeats, claim tasks, and post results back to the server. The
client wraps the server's REST API (``/api/v1/...``) endpoints.

The :class:`RemoteQueue` adapter implements the :class:`~monet.queue.TaskQueue`
protocol so ``run_worker()`` can use it transparently — the worker code
is identical whether dispatching locally or remotely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from monet.core.manifest import AgentCapability
    from monet.queue import TaskRecord
    from monet.types import AgentResult, AgentRunContext

__all__ = ["RemoteQueue", "WorkerClient"]

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

    async def register(
        self,
        pool: str,
        capabilities: list[AgentCapability],
        worker_id: str,
    ) -> str:
        """Register capabilities with the server. Returns deployment_id."""
        resp = await self._client.post(
            "/worker/register",
            json={
                "pool": pool,
                "capabilities": capabilities,
                "worker_id": worker_id,
            },
        )
        resp.raise_for_status()
        return str(resp.json()["deployment_id"])

    async def heartbeat(
        self,
        worker_id: str,
        pool: str,
        capabilities: list[AgentCapability] | None = None,
    ) -> None:
        """Send a heartbeat to the server.

        Args:
            worker_id: This worker's identifier.
            pool: Pool this worker claims from.
            capabilities: Current capability list. When provided, the
                server reconciles its manifest — declaring new/updated
                capabilities and removing stale ones for this worker.
        """
        payload: dict[str, object] = {"worker_id": worker_id, "pool": pool}
        if capabilities is not None:
            payload["capabilities"] = capabilities
        resp = await self._client.post("/worker/heartbeat", json=payload)
        resp.raise_for_status()

    async def claim(self, pool: str) -> TaskRecord | None:
        """Claim the next pending task. Returns None if nothing available."""
        resp = await self._client.get(f"/tasks/claim/{pool}")
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return resp.json()  # type: ignore[return-value]

    async def complete(self, task_id: str, result: AgentResult) -> None:
        """Post a successful result for a claimed task."""
        resp = await self._client.post(
            f"/tasks/{task_id}/complete",
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

    async def fail(self, task_id: str, error: str) -> None:
        """Post a failure for a claimed task."""
        resp = await self._client.post(
            f"/tasks/{task_id}/fail",
            json={"error": error},
        )
        resp.raise_for_status()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class RemoteQueue:
    """TaskQueue adapter wrapping :class:`WorkerClient`.

    Implements the consumer side of the ``TaskQueue`` protocol (claim,
    complete, fail) so ``run_worker()`` can use a remote server
    transparently. Producer methods (enqueue, poll_result) raise
    ``NotImplementedError`` — they are server-side concerns.
    """

    def __init__(self, client: WorkerClient, pool: str) -> None:
        self._client = client
        self._pool = pool

    async def enqueue(
        self,
        agent_id: str,
        command: str,
        ctx: AgentRunContext,
        pool: str = "local",
    ) -> str:
        raise NotImplementedError("enqueue is a server-side operation")

    async def poll_result(self, task_id: str, timeout: float = 600.0) -> AgentResult:
        raise NotImplementedError("poll_result is a server-side operation")

    async def claim(self, pool: str) -> TaskRecord | None:
        return await self._client.claim(pool)

    async def complete(self, task_id: str, result: AgentResult) -> None:
        await self._client.complete(task_id, result)

    async def fail(self, task_id: str, error: str) -> None:
        await self._client.fail(task_id, error)

    async def cancel(self, task_id: str) -> None:
        pass  # Server handles cancellation
