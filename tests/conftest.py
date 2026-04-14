"""Shared test fixtures for the monet test suite."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import pytest

from monet.catalogue import InMemoryCatalogueClient, configure_catalogue
from monet.core.manifest import default_manifest
from monet.core.registry import default_registry
from monet.orchestration._invoke import configure_queue
from monet.queue import InMemoryTaskQueue, run_worker

if TYPE_CHECKING:
    from monet.types import AgentRunContext


def make_ctx(**overrides: Any) -> AgentRunContext:
    """Build an AgentRunContext dict with defaults."""
    base: AgentRunContext = {
        "task": "",
        "context": [],
        "command": "fast",
        "trace_id": "",
        "run_id": "",
        "agent_id": "",
        "skills": [],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


@pytest.fixture
def clean_registry() -> Any:
    """Isolate each test from registry and manifest side effects."""
    with default_registry.registry_scope(), default_manifest.manifest_scope():
        yield


@pytest.fixture
def catalogue() -> Any:
    """Provide an in-memory catalogue backend."""
    configure_catalogue(InMemoryCatalogueClient())
    yield
    configure_catalogue(None)


@pytest.fixture(autouse=True)
async def _queue_worker() -> Any:
    """Wire an in-memory queue + worker for every async test.

    This makes ``invoke_agent`` work transparently: tasks enqueued by
    orchestration are claimed and executed by the background worker via
    the local handler registry.
    """
    queue = InMemoryTaskQueue()
    configure_queue(queue)
    worker_task = asyncio.create_task(run_worker(queue, default_registry))
    yield
    worker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker_task
    configure_queue(None)
