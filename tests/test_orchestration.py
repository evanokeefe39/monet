"""Tests for the orchestration layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from monet import agent
from monet.core.registry import (
    default_registry,  # internal: needed for registry_scope test fixture
)

if TYPE_CHECKING:
    from monet.types import AgentRunContext


def _ctx(**overrides: object) -> AgentRunContext:
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
def _clean_registry() -> None:  # type: ignore[misc]
    with default_registry.registry_scope():
        yield


# --- Mock agents ---


@agent(agent_id="orch-planner")
async def mock_planner(task: str) -> str:
    return f"Plan: {task}"


# --- invoke_agent transport ---


async def test_invoke_agent_local() -> None:
    from monet.orchestration import invoke_agent

    result = await invoke_agent("orch-planner", task="Test invoke")
    assert result.success is True
    assert isinstance(result.output, str)
    assert "Test invoke" in result.output


async def test_invoke_agent_missing_returns_failure() -> None:
    """Unknown agents route to pool='local' and surface as a failed result.

    Manifest is no longer the source of truth for existence — workers
    advertise capabilities via heartbeat; a missing handler in the worker
    registry produces an AgentResult with ``success=False`` rather than
    a synchronous ValueError from the orchestrator.
    """
    from monet.orchestration import invoke_agent

    result = await invoke_agent("ghost", task="x")
    assert result.success is False
