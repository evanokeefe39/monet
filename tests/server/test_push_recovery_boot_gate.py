"""Regression tests for push-dispatch restart-recovery boot gate.

Verifies that the server hard-fails at lifespan startup when in-flight
push-dispatch records exist but MONET_API_KEY or MONET_SERVER_URL is unset,
and proceeds normally when those records are absent (preserving keyless dev
mode for pull-only pools).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_push_records(count: int = 1) -> list[dict]:
    return [
        {
            "task_id": f"task-{i}",
            "attempt": 0,
            "url": "https://worker.example.com/dispatch",
            "dispatch_secret": None,
            "task_payload": '{"agent_id":"a"}',
        }
        for i in range(count)
    ]


async def test_no_records_passes_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no in-flight records exist, lifespan boots regardless of credentials."""
    from monet.queue.backends.redis_streams import RedisStreamsTaskQueue

    queue = MagicMock(spec=RedisStreamsTaskQueue)
    queue.list_in_flight_push_dispatches = AsyncMock(return_value=[])

    from monet.server._aegra_routes import _reissue_in_flight_push

    # Should complete without error even with no API key set.
    monkeypatch.delenv("MONET_API_KEY", raising=False)
    await _reissue_in_flight_push(queue)


async def test_no_records_non_redis_queue_passes() -> None:
    """Non-Redis queue skips recovery entirely."""
    from monet.queue.backends.memory import InMemoryTaskQueue
    from monet.server._aegra_routes import _reissue_in_flight_push

    await _reissue_in_flight_push(InMemoryTaskQueue())


async def test_push_records_missing_api_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hard-fail when in-flight push records exist and MONET_API_KEY is unset."""
    from monet.queue.backends.redis_streams import RedisStreamsTaskQueue

    queue = MagicMock(spec=RedisStreamsTaskQueue)
    queue.list_in_flight_push_dispatches = AsyncMock(return_value=_make_push_records(2))

    monkeypatch.delenv("MONET_API_KEY", raising=False)
    monkeypatch.setenv("MONET_SERVER_URL", "http://localhost:2026")

    from monet.server._aegra_routes import _reissue_in_flight_push

    with pytest.raises(RuntimeError, match="MONET_API_KEY"):
        await _reissue_in_flight_push(queue)


async def test_push_records_missing_server_url_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hard-fail when in-flight push records exist and MONET_SERVER_URL is unset."""
    from monet.queue.backends.redis_streams import RedisStreamsTaskQueue

    queue = MagicMock(spec=RedisStreamsTaskQueue)
    queue.list_in_flight_push_dispatches = AsyncMock(return_value=_make_push_records(1))

    monkeypatch.setenv("MONET_API_KEY", "secret-key")
    monkeypatch.delenv("MONET_SERVER_URL", raising=False)

    from monet.server._aegra_routes import _reissue_in_flight_push

    with pytest.raises(RuntimeError, match="MONET_SERVER_URL"):
        await _reissue_in_flight_push(queue)


async def test_push_records_with_credentials_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When credentials are present and records exist, tasks are re-dispatched."""

    from monet.queue.backends.redis_streams import RedisStreamsTaskQueue

    records = _make_push_records(1)
    queue = MagicMock(spec=RedisStreamsTaskQueue)
    queue.list_in_flight_push_dispatches = AsyncMock(return_value=records)

    monkeypatch.setenv("MONET_API_KEY", "secret-key")
    monkeypatch.setenv("MONET_SERVER_URL", "http://localhost:2026")

    created: list = []

    def _fake_create_task(coro: Any) -> MagicMock:
        # Close the coroutine to avoid ResourceWarning.
        coro.close()
        m = MagicMock()
        created.append(m)
        return m

    with (
        patch("monet.orchestration._invoke._push_with_retry", new_callable=AsyncMock),
        patch(
            "monet.server._aegra_routes.asyncio.create_task",
            side_effect=_fake_create_task,
        ),
        patch("monet.server._auth.task_hmac", return_value="tok"),
    ):
        from monet.server._aegra_routes import _reissue_in_flight_push

        await _reissue_in_flight_push(queue)

    # create_task should be called once per non-exhausted record.
    assert len(created) == 1


async def test_exhausted_push_record_fails_not_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausted push records are failed, not re-dispatched."""

    from monet.orchestration._invoke import _PUSH_MAX_ATTEMPTS
    from monet.queue.backends.redis_streams import RedisStreamsTaskQueue

    exhausted = [
        {
            "task_id": "task-exhausted",
            "attempt": _PUSH_MAX_ATTEMPTS,
            "url": "https://worker.example.com/dispatch",
            "dispatch_secret": None,
            "task_payload": '{"agent_id":"a"}',
        }
    ]
    queue = MagicMock(spec=RedisStreamsTaskQueue)
    queue.list_in_flight_push_dispatches = AsyncMock(return_value=exhausted)

    monkeypatch.setenv("MONET_API_KEY", "secret-key")
    monkeypatch.setenv("MONET_SERVER_URL", "http://localhost:2026")

    created: list = []

    def _fake_create_task(coro: Any) -> MagicMock:
        coro.close()
        m = MagicMock()
        created.append(m)
        return m

    with (
        patch(
            "monet.orchestration._invoke._write_dispatch_failed", new_callable=AsyncMock
        ) as mock_fail,
        patch(
            "monet.server._aegra_routes.asyncio.create_task",
            side_effect=_fake_create_task,
        ),
    ):
        from monet.server._aegra_routes import _reissue_in_flight_push

        await _reissue_in_flight_push(queue)

        mock_fail.assert_called_once()
        assert len(created) == 0
