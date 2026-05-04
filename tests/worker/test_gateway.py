"""Tests for the worker data plane gateway."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from monet.artifacts._memory import InMemoryArtifactClient
from monet.worker.gateway import (
    DEV_SIGNING_KEY,
    GatewayContext,
    create_gateway_app,
    mint_task_token,
    validate_token,
)

# ---------------------------------------------------------------------------
# Progress writer stub
# ---------------------------------------------------------------------------


class _StubProgressWriter:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, Any]] = []

    async def record(self, run_id: str, event: Any) -> int:
        self.recorded.append((run_id, event))
        return len(self.recorded)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    task_id: str = "task-1",
    run_id: str = "run-1",
    pool: str = "local",
    scopes: list[str] | None = None,
    signing_key: str = DEV_SIGNING_KEY,
    ttl_s: float = 3600.0,
) -> str:
    return mint_task_token(
        task_id=task_id,
        run_id=run_id,
        pool=pool,
        scopes=scopes or ["artifacts", "progress", "signals"],
        signing_key=signing_key,
        ttl_s=ttl_s,
    )


def _make_app() -> tuple[Any, _StubProgressWriter]:
    writer = _StubProgressWriter()
    ctx = GatewayContext(
        artifact_client=InMemoryArtifactClient(),
        progress_writer=writer,
        signing_key=DEV_SIGNING_KEY,
    )
    return create_gateway_app(ctx), writer


# ---------------------------------------------------------------------------
# JWT unit tests (no HTTP)
# ---------------------------------------------------------------------------


def test_jwt_roundtrip() -> None:
    """mint_task_token → validate_token roundtrip returns correct claims."""
    token = _make_token(task_id="t1", run_id="r1", pool="dev")
    claims = validate_token(token, DEV_SIGNING_KEY)
    assert claims["task_id"] == "t1"
    assert claims["run_id"] == "r1"
    assert claims["pool"] == "dev"
    assert "exp" in claims


def test_jwt_expired_raises() -> None:
    """validate_token raises ValueError for an expired token."""
    token = _make_token(ttl_s=-1.0)
    with pytest.raises(ValueError, match="expired"):
        validate_token(token, DEV_SIGNING_KEY)


def test_jwt_tampered_signature_raises() -> None:
    """validate_token raises ValueError when signature does not match."""
    token = _make_token()
    header, payload, sig = token.split(".")
    tampered = f"{header}.{payload}.{sig[:-4]}XXXX"
    with pytest.raises(ValueError, match="signature"):
        validate_token(tampered, DEV_SIGNING_KEY)


# ---------------------------------------------------------------------------
# HTTP tests
# ---------------------------------------------------------------------------


@pytest.fixture
def client_and_writer() -> tuple[httpx.AsyncClient, _StubProgressWriter]:
    app, writer = _make_app()
    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    return http_client, writer


@pytest.mark.asyncio
async def test_health_no_auth(
    client_and_writer: tuple[httpx.AsyncClient, _StubProgressWriter],
) -> None:
    """GET /health returns 200 without any auth header."""
    client, _ = client_and_writer
    async with client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_missing_auth_header_returns_401(
    client_and_writer: tuple[httpx.AsyncClient, _StubProgressWriter],
) -> None:
    """Request without Authorization header returns 401."""
    client, _ = client_and_writer
    async with client:
        resp = await client.post(
            "/progress/task-1",
            json={"event_type": "agent_started"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_task_id_in_url_returns_401(
    client_and_writer: tuple[httpx.AsyncClient, _StubProgressWriter],
) -> None:
    """Token minted for task-1 rejected when URL contains task-2."""
    client, _ = client_and_writer
    token = _make_token(task_id="task-1")
    async with client:
        resp = await client.post(
            "/progress/task-2",
            headers={"Authorization": f"Bearer {token}"},
            json={"event_type": "agent_started"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_artifact_write_read_roundtrip(
    client_and_writer: tuple[httpx.AsyncClient, _StubProgressWriter],
) -> None:
    """POST /artifacts/{task_id} followed by GET returns the same bytes."""
    client, _ = client_and_writer
    token = _make_token(task_id="task-42")
    payload = b"hello artifact"

    async with client:
        # Write
        write_resp = await client.post(
            "/artifacts/task-42",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("result.bin", payload, "application/octet-stream")},
            data={"key": "my_result"},
        )
        assert write_resp.status_code == 200
        body = write_resp.json()
        assert body["key"] == "my_result"
        assert "artifact_id" in body

        # Read
        read_resp = await client.get(
            "/artifacts/task-42/my_result",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert read_resp.status_code == 200
    assert read_resp.content == payload


@pytest.mark.asyncio
async def test_progress_stored_in_writer(
    client_and_writer: tuple[httpx.AsyncClient, _StubProgressWriter],
) -> None:
    """POST /progress/{task_id} calls progress_writer.record with run_id from JWT."""
    client, writer = client_and_writer
    token = _make_token(task_id="task-3", run_id="run-xyz")
    event = {"event_type": "agent_started", "agent_id": "my_agent"}

    async with client:
        resp = await client.post(
            "/progress/task-3",
            headers={"Authorization": f"Bearer {token}"},
            json=event,
        )
    assert resp.status_code == 200
    assert resp.json()["event_id"] == 1
    assert len(writer.recorded) == 1
    recorded_run_id, recorded_event = writer.recorded[0]
    assert recorded_run_id == "run-xyz"
    assert recorded_event["event_type"] == "agent_started"


@pytest.mark.asyncio
async def test_signal_accumulate_and_retrieve(
    client_and_writer: tuple[httpx.AsyncClient, _StubProgressWriter],
) -> None:
    """POST /signals then GET /signals returns accumulated signals."""
    client, _ = client_and_writer
    token = _make_token(task_id="task-5")
    signal_a = {"type": "needs_review", "reason": "low confidence", "metadata": {}}
    signal_b = {
        "type": "rate_limited",
        "reason": "quota hit",
        "metadata": {"retries": 3},
    }

    async with client:
        r1 = await client.post(
            "/signals/task-5",
            headers={"Authorization": f"Bearer {token}"},
            json=signal_a,
        )
        assert r1.json()["count"] == 1

        r2 = await client.post(
            "/signals/task-5",
            headers={"Authorization": f"Bearer {token}"},
            json=signal_b,
        )
        assert r2.json()["count"] == 2

        get_resp = await client.get(
            "/signals/task-5",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert get_resp.status_code == 200
    signals = get_resp.json()["signals"]
    assert len(signals) == 2
    assert signals[0]["type"] == "needs_review"
    assert signals[1]["type"] == "rate_limited"


@pytest.mark.asyncio
async def test_valid_auth_wrong_task_id_in_url_returns_401(
    client_and_writer: tuple[httpx.AsyncClient, _StubProgressWriter],
) -> None:
    """Valid JWT for task-A cannot access routes scoped to task-B."""
    client, _ = client_and_writer
    token = _make_token(task_id="task-A")

    async with client:
        resp = await client.get(
            "/signals/task-B",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 401
