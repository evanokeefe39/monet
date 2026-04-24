import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monet.queue.backends.sqlite_store import SqliteProgressStore
from monet.server.routes._threads import TranscriptResponse, get_thread_transcript


@pytest.mark.asyncio
async def test_transcript_synthesis_ordering(tmp_path):
    """
    Regression test for progress event ordering.
    Verifies that AGENT_STARTED telemetry events appear AFTER the user message
    that triggered them in the unified transcript, even if timestamps are close.
    """
    db_path = tmp_path / "progress.db"
    store = SqliteProgressStore(db_path)

    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    # 1. Create a mock LangGraph history
    # The user message happens at T=1000ms
    t_user = 1000
    mock_snapshot = MagicMock()
    mock_snapshot.created_at = datetime.fromtimestamp(t_user / 1000.0, UTC).isoformat()
    mock_snapshot.values = {
        "messages": [{"id": "msg-1", "role": "user", "content": "Hello agent!"}]
    }
    # Mock the dict conversion used in robustness logic
    mock_snapshot.dict.return_value = {
        "created_at": mock_snapshot.created_at,
        "values": mock_snapshot.values,
    }

    mock_lg_client = MagicMock()
    mock_lg_client.threads.get_history = AsyncMock(return_value=[mock_snapshot])
    mock_lg_client.threads.get = AsyncMock(return_value={"thread_id": thread_id})

    # 2. Inject telemetry into SQLite
    # The AGENT_STARTED event happens at T=1001ms (just after user message)
    t_started = 1001
    event = {
        "status": "AGENT_STARTED",
        "agent": "researcher",
        "command": "search",
        "run_id": run_id,
        "task_id": task_id,
        "thread_id": thread_id,
        "timestamp_ms": t_started,
    }
    await store.publish_progress(task_id, event)

    # 3. Call synthesis logic directly
    with patch("langgraph_sdk.get_client", return_value=mock_lg_client):
        resp: TranscriptResponse = await get_thread_transcript(thread_id, store)

    # 4. Verify Ordering
    entries = resp.entries
    assert len(entries) >= 2

    # Find user message and telemetry
    user_msg = next(e for e in entries if e.type == "message")
    telemetry = next(e for e in entries if e.type == "telemetry")

    u_idx = entries.index(user_msg)
    t_idx = entries.index(telemetry)

    # CRITICAL: Telemetry must come AFTER user message
    assert t_idx > u_idx, (
        f"Ordering bug: Telemetry (idx {t_idx}) appeared before "
        f"User Message (idx {u_idx})"
    )
    assert telemetry.data["status"] == "AGENT_STARTED"

    print(
        "\n[PASSED] Transcript synthesis correctly orders telemetry after "
        "user messages."
    )


@pytest.mark.asyncio
async def test_transcript_synthesis_priority_tiebreak(tmp_path):
    """
    Verifies that if timestamps are EXACTLY the same, priority still ensures
    the user message comes first.
    """
    db_path = tmp_path / "progress_tie.db"
    store = SqliteProgressStore(db_path)

    thread_id = str(uuid.uuid4())
    t_shared = 5000  # Same millisecond

    mock_snapshot = MagicMock()
    mock_snapshot.created_at = datetime.fromtimestamp(
        t_shared / 1000.0, UTC
    ).isoformat()
    mock_snapshot.values = {
        "messages": [{"id": "m1", "role": "user", "content": "sync"}]
    }
    mock_snapshot.dict.return_value = {
        "created_at": mock_snapshot.created_at,
        "values": mock_snapshot.values,
    }

    mock_lg_client = MagicMock()
    mock_lg_client.threads.get_history = AsyncMock(return_value=[mock_snapshot])
    mock_lg_client.threads.get = AsyncMock(return_value={})

    # Telemetry at same millisecond
    event = {
        "status": "AGENT_STARTED",
        "agent": "worker",
        "thread_id": thread_id,
        "timestamp_ms": t_shared,
    }
    await store.publish_progress("t1", event)

    with patch("langgraph_sdk.get_client", return_value=mock_lg_client):
        resp = await get_thread_transcript(thread_id, store)

    # Verify entries exist and are ordered by priority
    entries = resp.entries
    assert len(entries) >= 2
    user_msg = next(e for e in entries if e.type == "message")
    telemetry = next(e for e in entries if e.type == "telemetry")
    u_idx = entries.index(user_msg)
    t_idx = entries.index(telemetry)
    # Priority tiebreak: user (priority 0) before telemetry (priority 1)
    assert t_idx > u_idx


@pytest.mark.asyncio
async def test_transcript_synthesis_drift_correction(tmp_path):
    """
    Verifies that if telemetry arrives slightly BEFORE the user message
    (due to clock drift or late checkpointing), it is still sorted after
    the user message because they are in the same WINDOW.
    """
    db_path = tmp_path / "progress_drift.db"
    store = SqliteProgressStore(db_path)

    thread_id = str(uuid.uuid4())
    # Telemetry at T=10s
    t_telemetry = 10000
    # User message at T=20s (10s later! simulates slow first node)
    t_user = 20000

    mock_snapshot = MagicMock()
    mock_snapshot.created_at = datetime.fromtimestamp(t_user / 1000.0, UTC).isoformat()
    mock_snapshot.values = {
        "messages": [{"id": "m1", "role": "user", "content": "very slow check"}]
    }
    mock_snapshot.dict.return_value = {
        "created_at": mock_snapshot.created_at,
        "values": mock_snapshot.values,
    }

    mock_lg_client = MagicMock()
    mock_lg_client.threads.get_history = AsyncMock(return_value=[mock_snapshot])
    mock_lg_client.threads.get = AsyncMock(return_value={})

    event = {
        "status": "agent:started",
        "agent": "slow_worker",
        "thread_id": thread_id,
        "timestamp_ms": t_telemetry,
    }
    await store.publish_progress("t1", event)

    with patch("langgraph_sdk.get_client", return_value=mock_lg_client):
        resp = await get_thread_transcript(thread_id, store)

    # User message (priority 0) should be before agent:started (priority 1)
    # even though User was 10s later, because WINDOW=60000ms.
    types = [e.type for e in resp.entries]
    assert types == ["message", "telemetry"], (
        f"Drift correction failed: expected [message, telemetry], got {types}"
    )
    print(
        "[PASSED] 10s drift correction correctly grouped and prioritised user message."
    )


@pytest.mark.asyncio
async def test_transcript_synthesis_priority_edge_cases(tmp_path):
    """
    Test synthesis with various roles and statuses to ensure user message always wins.
    Specifically tests 'human' vs 'user' role and 'agent:started' status strings.
    """
    db_path = tmp_path / "progress_finer.db"
    store = SqliteProgressStore(db_path)

    thread_id = str(uuid.uuid4())
    t_base = 10000

    # 1. Create a "human" message (common in LangGraph instead of "user")
    mock_snapshot = MagicMock()
    mock_snapshot.created_at = datetime.fromtimestamp(t_base / 1000.0, UTC).isoformat()
    mock_snapshot.values = {
        "messages": [{"id": "m1", "role": "human", "content": "I am a human"}]
    }
    mock_snapshot.dict.return_value = {
        "created_at": mock_snapshot.created_at,
        "values": mock_snapshot.values,
    }

    # 2. Create telemetry with "agent:started" (actual status used in monet)
    events = [
        {
            "status": "agent:started",
            "agent": "planner",
            "thread_id": thread_id,
            "timestamp_ms": t_base + 10,
        },
        {
            "status": "searching",
            "agent": "researcher",
            "thread_id": thread_id,
            "timestamp_ms": t_base + 500,
        },
    ]
    for i, ev in enumerate(events):
        await store.publish_progress(f"task_{i}", ev)

    mock_lg_client = MagicMock()
    mock_lg_client.threads.get_history = AsyncMock(return_value=[mock_snapshot])
    mock_lg_client.threads.get = AsyncMock(return_value={})

    with patch("langgraph_sdk.get_client", return_value=mock_lg_client):
        resp = await get_thread_transcript(thread_id, store)

    # Validate ordering
    entries = resp.entries
    assert entries[0].type == "message"
    assert entries[0].data["role"] == "human"


@pytest.mark.asyncio
async def test_in_memory_queue_enrichment():
    """
    Verifies that InMemoryTaskQueue enriches events with timestamp_ms if missing.
    """
    from monet.queue.backends.memory import InMemoryTaskQueue

    queue = InMemoryTaskQueue()

    event = {"status": "progress", "msg": "no-ts"}
    await queue.publish_progress("task1", event)

    # Check the stored history
    history = await queue.get_thread_progress_history("task1")
    assert len(history) == 1
    assert "timestamp_ms" in history[0]
    assert isinstance(history[0]["timestamp_ms"], int)
    print("[PASSED] InMemoryTaskQueue correctly enriched event with timestamp_ms.")
