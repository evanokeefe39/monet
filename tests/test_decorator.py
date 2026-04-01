"""Tests for the @agent decorator."""

from __future__ import annotations

import asyncio

import pytest

from monet._decorator import agent
from monet._registry import default_registry
from monet._types import AgentRunContext
from monet.exceptions import EscalationRequired, NeedsHumanReview, SemanticError


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    """Isolate each test from registry side effects."""
    with default_registry.registry_scope():
        yield


# --- Basic decoration and invocation ---


async def test_minimal_agent() -> None:
    @agent(agent_id="test-minimal")
    async def my_agent(task: str) -> str:
        return f"Done: {task}"

    ctx = AgentRunContext(
        task="hello", trace_id="t1", run_id="r1", agent_id="test-minimal"
    )
    result = await my_agent(ctx)
    assert result.success is True
    assert result.output == "Done: hello"
    assert result.trace_id == "t1"
    assert result.run_id == "r1"


async def test_sync_function() -> None:
    @agent(agent_id="test-sync")
    def sync_agent(task: str) -> str:
        return f"Sync: {task}"

    ctx = AgentRunContext(task="test", agent_id="test-sync")
    result = await sync_agent(ctx)
    assert result.success is True
    assert result.output == "Sync: test"


# --- Parameter injection ---


async def test_injects_only_declared_params() -> None:
    @agent(agent_id="test-partial")
    async def partial_agent(task: str, command: str) -> str:
        return f"{command}: {task}"

    ctx = AgentRunContext(
        task="analyze",
        command="deep",
        effort="high",
        agent_id="test-partial",
    )
    result = await partial_agent(ctx)
    assert result.output == "deep: analyze"


async def test_injects_effort() -> None:
    @agent(agent_id="test-effort")
    async def effort_agent(task: str, effort: str) -> str:
        return f"effort={effort}"

    ctx = AgentRunContext(task="x", effort="low", agent_id="test-effort")
    result = await effort_agent(ctx)
    assert result.output == "effort=low"


async def test_injects_all_fields() -> None:
    @agent(agent_id="test-all")
    async def all_fields_agent(
        task: str,
        context: list,  # type: ignore[type-arg]
        command: str,
        effort: str,
        trace_id: str,
        run_id: str,
        agent_id: str,
        skills: list,  # type: ignore[type-arg]
    ) -> str:
        return f"{agent_id}/{command}"

    ctx = AgentRunContext(
        task="t",
        command="deep",
        effort="high",
        trace_id="tr",
        run_id="rn",
        agent_id="test-all",
        skills=["skill-a"],
    )
    result = await all_fields_agent(ctx)
    assert result.output == "test-all/deep"


# --- Decoration-time validation ---


def test_invalid_param_raises_at_decoration_time() -> None:
    with pytest.raises(TypeError, match="invalid_param"):

        @agent(agent_id="test-bad")
        async def bad_agent(task: str, invalid_param: str) -> str:
            return "never called"


def test_agent_id_required() -> None:
    with pytest.raises(TypeError, match="agent_id is required"):
        agent(lambda: None)


# --- Typed exception -> signals ---


async def test_needs_human_review_signal() -> None:
    @agent(agent_id="test-review")
    async def review_agent(task: str) -> str:
        raise NeedsHumanReview(reason="Low confidence")

    ctx = AgentRunContext(task="x", agent_id="test-review")
    result = await review_agent(ctx)
    assert result.success is False
    assert result.signals.needs_human_review is True
    assert result.signals.review_reason == "Low confidence"


async def test_escalation_signal() -> None:
    @agent(agent_id="test-escalate")
    async def escalation_agent(task: str) -> str:
        raise EscalationRequired(reason="Needs admin")

    ctx = AgentRunContext(task="x", agent_id="test-escalate")
    result = await escalation_agent(ctx)
    assert result.success is False
    assert result.signals.escalation_requested is True
    assert result.signals.escalation_reason == "Needs admin"


async def test_semantic_error_signal() -> None:
    @agent(agent_id="test-semantic")
    async def semantic_agent(task: str) -> str:
        raise SemanticError(type="no_results", message="Empty search")

    ctx = AgentRunContext(task="x", agent_id="test-semantic")
    result = await semantic_agent(ctx)
    assert result.success is False
    assert result.signals.semantic_error is not None
    assert result.signals.semantic_error.type == "no_results"
    assert result.signals.semantic_error.message == "Empty search"


async def test_unexpected_error_wrapped() -> None:
    @agent(agent_id="test-crash")
    async def crash_agent(task: str) -> str:
        raise ValueError("boom")

    ctx = AgentRunContext(task="x", agent_id="test-crash")
    result = await crash_agent(ctx)
    assert result.success is False
    assert result.signals.semantic_error is not None
    assert result.signals.semantic_error.type == "unexpected_error"
    assert "boom" in result.signals.semantic_error.message


# --- Registry integration ---


async def test_decorator_registers_handler() -> None:
    @agent(agent_id="test-reg")
    async def registered_agent(task: str) -> str:
        return "registered"

    handler = default_registry.lookup("test-reg", "fast")
    assert handler is registered_agent


async def test_custom_command_registration() -> None:
    @agent(agent_id="test-cmd", command="deep")
    async def deep_agent(task: str) -> str:
        return "deep"

    assert default_registry.lookup("test-cmd", "deep") is deep_agent
    assert default_registry.lookup("test-cmd", "fast") is None


# --- Concurrent invocation isolation ---


async def test_concurrent_context_isolation() -> None:
    """ContextVar must not bleed between concurrent invocations."""

    @agent(agent_id="test-concurrent")
    async def concurrent_agent(task: str, trace_id: str) -> str:
        await asyncio.sleep(0.01)  # Force interleaving
        return f"{task}|{trace_id}"

    results = await asyncio.gather(
        concurrent_agent(
            AgentRunContext(task="A", trace_id="t-a", agent_id="test-concurrent")
        ),
        concurrent_agent(
            AgentRunContext(task="B", trace_id="t-b", agent_id="test-concurrent")
        ),
        concurrent_agent(
            AgentRunContext(task="C", trace_id="t-c", agent_id="test-concurrent")
        ),
    )

    assert results[0].output == "A|t-a"
    assert results[0].trace_id == "t-a"
    assert results[1].output == "B|t-b"
    assert results[1].trace_id == "t-b"
    assert results[2].output == "C|t-c"
    assert results[2].trace_id == "t-c"
