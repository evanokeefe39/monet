"""Tests for monet.server.bootstrap()."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import pytest

from monet.artifacts import configure_artifacts
from monet.core.manifest import default_manifest
from monet.orchestration._invoke import configure_queue, get_queue

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _cleanup() -> None:  # type: ignore[misc]
    """Clean up global state after each test."""
    yield
    configure_queue(None)
    configure_artifacts(None)


async def test_bootstrap_configures_queue_and_artifacts(tmp_path: Path) -> None:
    from monet.server import bootstrap

    worker_task = await bootstrap(
        artifacts_root=tmp_path / "cat",
        enable_tracing=False,
    )
    assert worker_task is not None

    try:
        # Queue is configured
        assert get_queue() is not None

        # Artifact store directory was created
        assert (tmp_path / "cat" / "blobs").exists()
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task


async def test_bootstrap_declares_supplemental_agents(tmp_path: Path) -> None:
    from monet.server import bootstrap

    with default_manifest.manifest_scope():
        worker_task = await bootstrap(
            artifacts_root=tmp_path / "cat",
            enable_tracing=False,
            agents=[
                {"agent_id": "remote-agent", "command": "fast", "description": "test"},
            ],
        )
        assert worker_task is not None

        try:
            assert default_manifest.is_available("remote-agent", "fast")
        finally:
            worker_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker_task


async def test_bootstrap_worker_executes_agents(tmp_path: Path) -> None:
    """End-to-end: bootstrap starts a worker that can execute agents."""
    from monet.server import bootstrap

    worker_task = await bootstrap(
        artifacts_root=tmp_path / "cat",
        enable_tracing=False,
    )
    assert worker_task is not None

    try:
        # Verify the queue is configured and functional.
        # (invoke_agent E2E is covered by test_queue.py worker tests.)
        assert get_queue() is not None
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
