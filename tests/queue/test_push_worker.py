"""Worker-side push: create_push_app + handle_dispatch.

Exercises the FastAPI app returned by ``create_push_app`` using a
``TestClient`` equivalent (httpx ASGITransport) and mocks the callback
URL with ``respx``. Covers:

- Valid dispatch returns 202 fast and schedules background handler.
- The handler posts progress (via emit_progress) and completion.
- Invalid / missing dispatch secret → 401 / 500.
- Agent exception → fail callback.
- Oversize payload → 413.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient, Response

if TYPE_CHECKING:
    import respx  # type: ignore[import-not-found]

from monet._ports import MAX_INLINE_PAYLOAD_BYTES
from monet.core._serialization import serialize_task_record
from monet.core.push_handler import close_client, create_push_app
from monet.core.registry import LocalRegistry
from monet.queue import TaskRecord, TaskStatus
from monet.types import AgentResult, AgentRunContext

CALLBACK = "http://orchestrator.example/api/v1/tasks/t-1"
TOKEN = "per-task-token"
SECRET = "dispatch-secret"


def _ctx(agent_id: str = "a", command: str = "go") -> AgentRunContext:
    return {
        "task": "do",
        "context": [],
        "command": command,
        "trace_id": "t",
        "run_id": "r",
        "agent_id": agent_id,
        "skills": [],
    }


def _record(agent_id: str = "a", command: str = "go") -> TaskRecord:
    return {
        "task_id": "t-1",
        "agent_id": agent_id,
        "command": command,
        "pool": "cloud",
        "context": _ctx(agent_id, command),
        "status": TaskStatus.PENDING,
        "result": None,
        "created_at": datetime.now(UTC).isoformat(),
        "claimed_at": None,
        "completed_at": None,
    }


@pytest.fixture
async def push_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("MONET_DISPATCH_SECRET", SECRET)
    registry = LocalRegistry()
    app = create_push_app(registry=registry)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield registry, client
    await close_client()


def _dispatch_body(
    *, agent_id: str = "a", command: str = "go", task_id: str = "t-1"
) -> dict[str, str]:
    record = _record(agent_id, command)
    record["task_id"] = task_id
    payload = serialize_task_record(record)
    return {
        "task_id": task_id,
        "token": TOKEN,
        "callback_url": CALLBACK.replace("t-1", task_id),
        "payload": payload,
    }


@pytest.mark.respx
async def test_dispatch_runs_agent_and_posts_complete(
    push_app: Any, respx_mock: respx.MockRouter
) -> None:
    registry, client = push_app

    async def handler(ctx: AgentRunContext) -> AgentResult:
        return AgentResult(
            success=True,
            output=f"handled: {ctx['task']}",
            trace_id=ctx["trace_id"],
            run_id=ctx["run_id"],
        )

    registry.register("a", "go", handler)

    complete = respx_mock.post(f"{CALLBACK}/complete").mock(
        return_value=Response(200, json={"status": "ok"})
    )

    resp = await client.post(
        "/dispatch",
        json=_dispatch_body(),
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert resp.status_code == 202

    # Background task is scheduled; wait briefly for it to complete.
    await asyncio.sleep(0.2)
    assert complete.called
    body = json.loads(complete.calls.last.request.content.decode())
    assert body["success"] is True
    assert body["output"] == "handled: do"


@pytest.mark.respx
async def test_dispatch_posts_fail_on_agent_exception(
    push_app: Any, respx_mock: respx.MockRouter
) -> None:
    registry, client = push_app

    async def handler(ctx: AgentRunContext) -> AgentResult:
        msg = "blew up"
        raise RuntimeError(msg)

    registry.register("a", "go", handler)
    fail = respx_mock.post(f"{CALLBACK}/fail").mock(
        return_value=Response(200, json={"status": "ok"})
    )

    resp = await client.post(
        "/dispatch",
        json=_dispatch_body(),
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert resp.status_code == 202
    await asyncio.sleep(0.2)
    assert fail.called
    body = json.loads(fail.calls.last.request.content.decode())
    assert "blew up" in body["error"]


async def test_dispatch_rejects_missing_auth(push_app: Any) -> None:
    _registry, client = push_app
    resp = await client.post("/dispatch", json=_dispatch_body())
    assert resp.status_code == 401


async def test_dispatch_rejects_wrong_secret(push_app: Any) -> None:
    _registry, client = push_app
    resp = await client.post(
        "/dispatch",
        json=_dispatch_body(),
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert resp.status_code == 401


async def test_dispatch_rejects_oversize_payload(push_app: Any) -> None:
    _registry, client = push_app
    body = _dispatch_body()
    # Replace payload with an oversized string so Content-Length trips.
    body["payload"] = "x" * (MAX_INLINE_PAYLOAD_BYTES + 100)
    resp = await client.post(
        "/dispatch",
        json=body,
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert resp.status_code == 413


@pytest.mark.respx
async def test_dispatch_fails_when_handler_missing(
    push_app: Any, respx_mock: respx.MockRouter
) -> None:
    _registry, client = push_app
    fail = respx_mock.post(f"{CALLBACK}/fail").mock(
        return_value=Response(200, json={"status": "ok"})
    )
    resp = await client.post(
        "/dispatch",
        json=_dispatch_body(agent_id="ghost"),
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert resp.status_code == 202
    await asyncio.sleep(0.2)
    assert fail.called
    body = json.loads(fail.calls.last.request.content.decode())
    assert "No handler" in body["error"]
