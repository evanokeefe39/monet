"""Shared test fixtures for the monet test suite."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import pytest

from monet.artifacts import InMemoryArtifactClient, configure_artifacts
from monet.config import MONET_ENV_VARS
from monet.core.registry import default_registry
from monet.orchestration._invoke import configure_queue
from monet.queue import InMemoryTaskQueue
from monet.worker import run_worker

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


@pytest.fixture(autouse=True)
def _clean_monet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delenv every ``MONET_*`` variable before each test.

    Config tests that need a specific value set it via
    ``monkeypatch.setenv``. Without this, a test that sets
    ``MONET_API_KEY=xxx`` leaks that value into a later test that
    expects it unset — the silent-failure class this whole hardening
    pass exists to prevent.
    """
    for name in MONET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def clean_registry() -> Any:
    """Isolate each test from registry side effects."""
    with default_registry.registry_scope():
        yield


@pytest.fixture
def artifacts() -> Any:
    """Provide an in-memory artifact store backend."""
    configure_artifacts(InMemoryArtifactClient())
    yield
    configure_artifacts(None)


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
