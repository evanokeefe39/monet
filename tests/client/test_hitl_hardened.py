"""Phase 4 HITL hardening tests.

Server-side cause_id validation and client-side split-plane ordering.
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from monet.client import AlreadyResolved, MonetClient
from monet.client._wire import post_hitl_decision
from monet.events import EventType, ProgressEvent
from monet.progress.backends.sqlite import SqliteProgressBackend
from monet.server import create_data_app

_API_KEY = "test-key"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> SqliteProgressBackend:
    return SqliteProgressBackend(":memory:")


@pytest.fixture
def data_client(
    backend: SqliteProgressBackend, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setenv("MONET_API_KEY", _API_KEY)
    app = create_data_app(writer=backend, reader=backend)
    return TestClient(app, raise_server_exceptions=True)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_API_KEY}"}


def _cause_event(run_id: str, cause_id: str) -> ProgressEvent:
    return {
        "event_id": 0,
        "run_id": run_id,
        "task_id": "task-1",
        "agent_id": "agent-1",
        "event_type": EventType.HITL_CAUSE,
        "timestamp_ms": int(time.time() * 1000),
        "payload": {"cause_id": cause_id},
    }


# ---------------------------------------------------------------------------
# 4.1 — cause_id validation on POST /runs/{run_id}/events
# ---------------------------------------------------------------------------


def test_hitl_decision_without_cause_id_returns_400(
    data_client: TestClient,
) -> None:
    body = {
        "task_id": "task-1",
        "agent_id": "agent-1",
        "event_type": "hitl_decision",
        "timestamp_ms": int(time.time() * 1000),
        "payload": {},
    }
    resp = data_client.post("/api/v1/runs/run-1/events", json=body, headers=_auth())
    assert resp.status_code == 400
    assert "cause_id" in resp.json()["detail"]


def test_hitl_decision_with_unknown_cause_returns_400(
    data_client: TestClient,
) -> None:
    body = {
        "task_id": "task-1",
        "agent_id": "agent-1",
        "event_type": "hitl_decision",
        "timestamp_ms": int(time.time() * 1000),
        "payload": {"cause_id": "no-such-cause"},
    }
    resp = data_client.post("/api/v1/runs/run-1/events", json=body, headers=_auth())
    assert resp.status_code == 400
    assert "hitl_cause" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_hitl_decision_with_valid_cause_accepted(
    data_client: TestClient, backend: SqliteProgressBackend
) -> None:
    run_id = "run-valid"
    cause_id = str(uuid.uuid4())
    await backend.record(run_id, _cause_event(run_id, cause_id))

    body = {
        "task_id": "task-1",
        "agent_id": "human",
        "event_type": "hitl_decision",
        "timestamp_ms": int(time.time() * 1000),
        "payload": {"cause_id": cause_id},
    }
    resp = data_client.post(f"/api/v1/runs/{run_id}/events", json=body, headers=_auth())
    assert resp.status_code == 202


# ---------------------------------------------------------------------------
# 4.2 — duplicate decision returns 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_hitl_decision_returns_409(
    data_client: TestClient, backend: SqliteProgressBackend
) -> None:
    run_id = "run-dup"
    cause_id = str(uuid.uuid4())
    await backend.record(run_id, _cause_event(run_id, cause_id))

    body = {
        "task_id": "task-1",
        "agent_id": "human",
        "event_type": "hitl_decision",
        "timestamp_ms": int(time.time() * 1000),
        "payload": {"cause_id": cause_id},
    }
    # First decision accepted
    resp1 = data_client.post(
        f"/api/v1/runs/{run_id}/events", json=body, headers=_auth()
    )
    assert resp1.status_code == 202

    # Second decision rejected as duplicate
    resp2 = data_client.post(
        f"/api/v1/runs/{run_id}/events", json=body, headers=_auth()
    )
    assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# 4.2 — has_decision backend protocol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_has_decision_false_before_record(
    backend: SqliteProgressBackend,
) -> None:
    cause_id = str(uuid.uuid4())
    assert not await backend.has_decision("run-x", cause_id)


@pytest.mark.asyncio
async def test_has_decision_true_after_record(
    backend: SqliteProgressBackend,
) -> None:
    run_id = "run-hd"
    cause_id = str(uuid.uuid4())
    event: ProgressEvent = {
        "event_id": 0,
        "run_id": run_id,
        "task_id": "t",
        "agent_id": "human",
        "event_type": EventType.HITL_DECISION,
        "timestamp_ms": int(time.time() * 1000),
        "payload": {"cause_id": cause_id},
    }
    await backend.record(run_id, event)
    assert await backend.has_decision(run_id, cause_id)
    assert not await backend.has_decision(run_id, "other-cause")


# ---------------------------------------------------------------------------
# 4.3 — post_hitl_decision wire function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_hitl_decision_raises_already_resolved_on_409() -> None:
    import httpx
    import respx

    with respx.mock:
        respx.post("http://data:9000/runs/run-1/events").mock(
            return_value=httpx.Response(409, json={"detail": "already recorded"})
        )
        with pytest.raises(AlreadyResolved):
            await post_hitl_decision(
                "http://data:9000",
                "run-1",
                task_id="task-1",
                agent_id="human",
                cause_id="cid",
                tag="human_interrupt",
            )


@pytest.mark.asyncio
async def test_post_hitl_decision_succeeds_on_202() -> None:
    import httpx
    import respx

    with respx.mock:
        respx.post("http://data:9000/runs/run-1/events").mock(
            return_value=httpx.Response(202, json={"event_id": 5})
        )
        await post_hitl_decision(
            "http://data:9000",
            "run-1",
            task_id="task-1",
            agent_id="human",
            cause_id="cid",
            tag="human_interrupt",
        )


# ---------------------------------------------------------------------------
# 4.3 — MonetClient.resume() split-plane ordering
# ---------------------------------------------------------------------------


def _make_split_client() -> MonetClient:
    with (
        patch("monet.client._wire.make_client"),
        patch("monet.client.__init__.make_client"),
        patch("monet.config.load_entrypoints", return_value=[]),
        patch("monet.config.load_graph_roles", return_value={}),
        patch("monet.client.__init__.ChatClient"),
    ):
        return MonetClient("http://control:2026", data_plane_url="http://data:9000")


@pytest.mark.asyncio
async def test_post_hitl_decision_sends_correct_payload() -> None:
    """_post_hitl_decision queries events, finds cause_id, posts HITL_DECISION."""
    client = _make_split_client()
    cause_id = str(uuid.uuid4())
    fake_events = [
        {
            "event_id": 1,
            "run_id": "run-sp",
            "task_id": "task-1",
            "agent_id": "agent-1",
            "event_type": "hitl_cause",
            "timestamp_ms": 1000,
            "payload": {"cause_id": cause_id},
        }
    ]
    posted: list[dict[str, Any]] = []

    async def fake_query(
        url: str, run_id: str, *, api_key: Any, after: int, limit: int
    ) -> list[Any]:
        return fake_events

    async def fake_post(
        url: str,
        run_id: str,
        *,
        task_id: str,
        agent_id: str,
        cause_id: str,
        tag: str,
        api_key: Any = None,
        timestamp_ms: Any = None,
    ) -> None:
        posted.append({"cause_id": cause_id, "tag": tag})

    with (
        patch("monet.client._wire.query_progress_events", new=fake_query),
        patch("monet.client._wire.post_hitl_decision", new=fake_post),
    ):
        await client._runs._post_hitl_decision("run-sp", "human_interrupt")

    assert len(posted) == 1
    assert posted[0]["cause_id"] == cause_id
    assert posted[0]["tag"] == "human_interrupt"


@pytest.mark.asyncio
async def test_post_hitl_decision_swallows_already_resolved() -> None:
    """409 from data plane is swallowed — decision already recorded."""
    client = _make_split_client()
    cause_id = str(uuid.uuid4())
    fake_events = [
        {
            "event_id": 1,
            "run_id": "run-dup2",
            "task_id": "task-1",
            "agent_id": "agent-1",
            "event_type": "hitl_cause",
            "timestamp_ms": 1000,
            "payload": {"cause_id": cause_id},
        }
    ]

    async def fake_query(*a: Any, **kw: Any) -> list[Any]:
        return fake_events

    async def fake_post_409(*a: Any, **kw: Any) -> None:
        raise AlreadyResolved("run-dup2")

    with (
        patch("monet.client._wire.query_progress_events", new=fake_query),
        patch("monet.client._wire.post_hitl_decision", new=fake_post_409),
    ):
        # Must not raise even though post raises AlreadyResolved
        await client._runs._post_hitl_decision("run-dup2", "human_interrupt")


@pytest.mark.asyncio
async def test_post_hitl_decision_skips_when_no_cause_event() -> None:
    """No HITL_CAUSE events → post is skipped entirely."""
    client = _make_split_client()
    post_called = False

    async def fake_query(*a: Any, **kw: Any) -> list[Any]:
        return []

    async def fake_post(*a: Any, **kw: Any) -> None:
        nonlocal post_called
        post_called = True

    with (
        patch("monet.client._wire.query_progress_events", new=fake_query),
        patch("monet.client._wire.post_hitl_decision", new=fake_post),
    ):
        await client._runs._post_hitl_decision("run-no-cause", "human_interrupt")

    assert not post_called


@pytest.mark.asyncio
async def test_resume_calls_post_hitl_decision_in_split_plane() -> None:
    """resume() calls _post_hitl_decision when data_url differs from url."""
    client = _make_split_client()
    post_calls: list[tuple[str, str]] = []

    async def fake_post_hitl(run_id: str, tag: str) -> None:
        post_calls.append((run_id, tag))

    async def fake_stream(*a: Any, **kw: Any) -> Any:
        return
        yield  # async generator

    client._runs._find_interrupted_thread = AsyncMock(  # type: ignore[method-assign]
        return_value=("thread-1", "default")
    )
    client._runs._await_interrupted_status = AsyncMock()  # type: ignore[method-assign]
    client._runs._post_hitl_decision = fake_post_hitl  # type: ignore[method-assign]

    async def _fake_get_state(*a: Any, **kw: Any) -> tuple[Any, list[str]]:
        return {}, ["human_interrupt"]

    with (
        patch("monet.client._run.get_state_values", new=_fake_get_state),
        patch("monet.client._run.stream_run", new=fake_stream),
    ):
        await client.resume("run-sp", "human_interrupt", {"action": "retry"})

    assert post_calls == [("run-sp", "human_interrupt")]


@pytest.mark.asyncio
async def test_resume_skips_data_plane_post_when_unified() -> None:
    """resume() does NOT call _post_hitl_decision when data_url == url."""
    with (
        patch("monet.client._wire.make_client"),
        patch("monet.client.__init__.make_client"),
        patch("monet.config.load_entrypoints", return_value=[]),
        patch("monet.config.load_graph_roles", return_value={}),
        patch("monet.client.__init__.ChatClient"),
    ):
        client = MonetClient("http://unified:2026")  # no data_plane_url

    post_calls: list[tuple[str, str]] = []

    async def fake_post_hitl(run_id: str, tag: str) -> None:
        post_calls.append((run_id, tag))

    async def fake_stream(*a: Any, **kw: Any) -> Any:
        return
        yield  # async generator

    client._runs._find_interrupted_thread = AsyncMock(  # type: ignore[method-assign]
        return_value=("thread-1", "default")
    )
    client._runs._await_interrupted_status = AsyncMock()  # type: ignore[method-assign]
    client._runs._post_hitl_decision = fake_post_hitl  # type: ignore[method-assign]

    async def _fake_get_state(*a: Any, **kw: Any) -> tuple[Any, list[str]]:
        return {}, ["human_interrupt"]

    with (
        patch("monet.client._run.get_state_values", new=_fake_get_state),
        patch("monet.client._run.stream_run", new=fake_stream),
    ):
        await client.resume("run-unified", "human_interrupt", {"action": "retry"})

    assert post_calls == []
