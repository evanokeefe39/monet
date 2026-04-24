"""Tests for DispatchBackend protocol and LocalDispatchBackend."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from monet.queue._dispatch import ClaimedTask, DispatchBackend
from monet.queue.backends.dispatch_local import LocalDispatchBackend


def _make_task(**overrides: str) -> ClaimedTask:
    base: ClaimedTask = {
        "task_id": "task-1",
        "run_id": "run-1",
        "thread_id": "thread-1",
        "agent_id": "test_agent",
        "command": "fast",
        "pool": "local",
    }
    return {**base, **overrides}  # type: ignore[misc]


def test_dispatch_backend_protocol() -> None:
    """LocalDispatchBackend satisfies DispatchBackend protocol."""
    backend = LocalDispatchBackend()
    assert isinstance(backend, DispatchBackend)


@pytest.mark.asyncio
async def test_local_dispatch_spawns_subprocess() -> None:
    """submit() spawns a subprocess and does not await its completion."""
    backend = LocalDispatchBackend()
    task = _make_task(task_id="task-abc")

    mock_proc = AsyncMock()
    mock_proc.pid = 12345

    with patch(
        "asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ) as mock_spawn:
        await backend.submit(task, "http://localhost:2026", "test-key")

    mock_spawn.assert_called_once()
    call_args = mock_spawn.call_args
    cmd = call_args.args
    assert "--task-id" in cmd
    assert "task-abc" in cmd
    # Env vars injected
    env = call_args.kwargs["env"]
    assert env["MONET_API_KEY"] == "test-key"
    assert env["MONET_SERVER_URL"] == "http://localhost:2026"


@pytest.mark.asyncio
async def test_local_dispatch_no_await() -> None:
    """submit() returns immediately after spawning; does not wait for exit."""
    backend = LocalDispatchBackend()
    task = _make_task()

    async def _slow_process(*args, **kwargs):  # type: ignore[no-untyped-def]
        mock = AsyncMock()
        mock.pid = 1
        return mock

    with patch("asyncio.create_subprocess_exec", side_effect=_slow_process):
        # Should return quickly without awaiting process exit
        await asyncio.wait_for(
            backend.submit(task, "http://localhost:2026", "key"),
            timeout=1.0,
        )
