"""Worker-side push support: FastAPI dispatch app + batch-mode helper.

Push workers (Cloud Run Service, Lambda Function URL, ACA, ECS with
ALB) receive HTTP dispatches from monet's orchestrator. The provider
invokes the container per task rather than the worker polling a queue.

Two entry points:

- :func:`create_push_app` — FastAPI factory used by ``monet worker
  --push``. One route: ``POST /dispatch``. Validates the dispatch
  secret, spawns :func:`handle_dispatch` as a background task, returns
  ``202 Accepted`` immediately so the provider can autoscale on
  request count.
- :func:`handle_dispatch` — public helper that runs a decoded
  ``TaskRecord`` against the agent registry and POSTs progress +
  completion back to the orchestrator. Also usable directly from a
  batch-mode user script (Cloud Run Jobs, ECS Fargate Task, Lambda
  native event).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel

from monet._ports import MAX_INLINE_PAYLOAD_BYTES
from monet.core._serialization import deserialize_task_record
from monet.core.stubs import _progress_publisher

if TYPE_CHECKING:
    from fastapi import FastAPI

    from monet.core.registry import LocalRegistry

__all__ = ["DispatchBody", "create_push_app", "handle_dispatch"]

_log = logging.getLogger("monet.core.push_handler")

_CLIENT_TIMEOUT = 30.0
_client: httpx.AsyncClient | None = None

# Strong references to fire-and-forget background tasks so the event
# loop doesn't garbage-collect them mid-run. Tasks self-remove on
# completion via the add_done_callback below.
_bg_tasks: set[asyncio.Task[Any]] = set()


def _spawn_bg(coro: Any) -> asyncio.Task[Any]:
    """Schedule ``coro`` as a background task and keep a strong ref."""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


async def _get_client() -> httpx.AsyncClient:
    """Process-wide httpx client for callback POSTs."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_CLIENT_TIMEOUT)
    return _client


async def close_client() -> None:
    """Close the module-level httpx client. Called from app shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def handle_dispatch(
    *,
    payload: str,
    token: str,
    callback_url: str,
    registry: LocalRegistry | None = None,
) -> None:
    """Decode a TaskRecord, run the agent handler, and post callbacks.

    Args:
        payload: serialize_task_record-encoded TaskRecord (the
            ``payload`` field of the dispatch envelope).
        token: per-task HMAC bearer. Presented back to the orchestrator
            on every callback (progress, complete, fail).
        callback_url: orchestrator base URL for this task, typically
            ``{MONET_API_URL}/api/v1/tasks/{task_id}``.
        registry: agent handler registry. Defaults to the global
            registry populated by ``@agent`` decorators.
    """
    if registry is None:
        from monet.core.registry import default_registry

        registry = default_registry

    record = deserialize_task_record(payload)
    task_id = record["task_id"]
    agent_id = record["agent_id"]
    command = record["command"]

    client = await _get_client()
    headers = {"Authorization": f"Bearer {token}"}

    def _publisher(data: dict[str, Any]) -> None:
        """Fire-and-forget progress POST scheduled on the event loop."""
        _spawn_bg(_post_progress(client, callback_url, data, headers))

    pub_token = _progress_publisher.set(_publisher)
    try:
        handler = registry.lookup(agent_id, command)
        if handler is None:
            await _post_fail(
                client,
                callback_url,
                f"No handler for {agent_id}/{command} in push worker registry",
                headers,
            )
            return
        try:
            result = await handler(record["context"])
        except Exception as exc:
            _log.exception("Push handler for %s/%s failed", agent_id, command)
            await _post_fail(
                client,
                callback_url,
                f"{type(exc).__name__}: {exc}",
                headers,
            )
            return
        await _post_complete(client, callback_url, result, headers)
    finally:
        _progress_publisher.reset(pub_token)
    _log.info("Push dispatch complete for task %s", task_id)


async def _post_progress(
    client: httpx.AsyncClient,
    callback_url: str,
    data: dict[str, Any],
    headers: dict[str, str],
) -> None:
    try:
        resp = await client.post(f"{callback_url}/progress", json=data, headers=headers)
        if resp.status_code >= 400:
            _log.debug(
                "Progress POST returned %d for %s", resp.status_code, callback_url
            )
    except Exception:
        _log.debug("Progress POST failed for %s", callback_url, exc_info=True)


async def _post_complete(
    client: httpx.AsyncClient,
    callback_url: str,
    result: Any,
    headers: dict[str, str],
) -> None:
    # serialize_result returns a JSON string; parse back to a dict for
    # the route's pydantic model. Route expects explicit fields, not a
    # nested blob, so we send them directly.
    payload = {
        "success": result.success,
        "output": result.output,
        "artifacts": [dict(a) for a in result.artifacts],
        "signals": [dict(s) for s in result.signals],
        "trace_id": result.trace_id,
        "run_id": result.run_id,
    }
    try:
        resp = await client.post(
            f"{callback_url}/complete", json=payload, headers=headers
        )
        resp.raise_for_status()
    except Exception:
        _log.exception("Complete POST failed for %s", callback_url)


async def _post_fail(
    client: httpx.AsyncClient,
    callback_url: str,
    error: str,
    headers: dict[str, str],
) -> None:
    try:
        resp = await client.post(
            f"{callback_url}/fail", json={"error": error}, headers=headers
        )
        resp.raise_for_status()
    except Exception:
        _log.exception("Fail POST failed for %s", callback_url)


class DispatchBody(BaseModel):
    """Body for ``POST /dispatch``."""

    task_id: str
    token: str
    callback_url: str
    payload: str


def create_push_app(
    *,
    registry: LocalRegistry | None = None,
    dispatch_secret_env: str = "MONET_DISPATCH_SECRET",
) -> FastAPI:
    """FastAPI app with a single ``POST /dispatch`` route.

    The dispatch secret guards the endpoint against random internet
    traffic; it must match what the orchestrator sends in the
    ``Authorization: Bearer`` header. Reads from ``os.environ`` at
    request time rather than factory call time so tests can override.

    Args:
        registry: agent handler registry (defaults to the global).
        dispatch_secret_env: env var holding the dispatch secret.
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, Header, HTTPException

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> Any:
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                await close_client()

    app = FastAPI(title="monet push worker", lifespan=_lifespan)

    @app.post("/dispatch", status_code=202)
    async def dispatch(
        body: DispatchBody,
        authorization: str | None = Header(default=None),
        content_length: str | None = Header(default=None, alias="content-length"),
    ) -> dict[str, str]:
        secret = os.environ.get(dispatch_secret_env)
        if not secret:
            raise HTTPException(500, f"{dispatch_secret_env} not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Missing Bearer token")
        presented = authorization.removeprefix("Bearer ")
        if not hmac.compare_digest(presented, secret):
            raise HTTPException(401, "Invalid dispatch secret")

        if content_length is not None:
            try:
                size = int(content_length)
            except ValueError as exc:
                raise HTTPException(400, "Invalid Content-Length") from exc
            if size > MAX_INLINE_PAYLOAD_BYTES:
                raise HTTPException(
                    413,
                    f"Dispatch payload {size} bytes exceeds "
                    f"MAX_INLINE_PAYLOAD_BYTES={MAX_INLINE_PAYLOAD_BYTES}",
                )

        _spawn_bg(
            handle_dispatch(
                payload=body.payload,
                token=body.token,
                callback_url=body.callback_url,
                registry=registry,
            )
        )
        return {"status": "accepted", "task_id": body.task_id}

    return app
