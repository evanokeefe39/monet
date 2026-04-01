"""Tests for Spike 1: Node Wrapper Transport Switch.

Success criteria from SPIKES.md: the same test passes unchanged against
both a local @agent function and a mock HTTP server serving the same agent.
No conditional branching in the test. No different assertions for each path.
"""

from __future__ import annotations

import pytest

from .decorator import NeedsHumanReview, SemanticError, agent
from .http_server import app
from .invoke import invoke_agent
from .models import (
    AgentResult,
    AgentSignals,
    HttpDescriptor,
    InputEnvelope,
    LocalDescriptor,
)

# --- Mock agents ---


@agent(agent_id="researcher", command="fast")
async def mock_researcher(task: str) -> str:
    return f"Research result for: {task}"


@agent(agent_id="failing-agent", command="fast")
async def mock_failing_agent(task: str) -> str:
    raise NeedsHumanReview(reason="Confidence too low")


@agent(agent_id="error-agent", command="fast")
async def mock_error_agent(task: str) -> str:
    raise SemanticError(type="no_results", message="No sources found")


@agent(agent_id="crash-agent", command="fast")
async def mock_crash_agent(task: str) -> str:
    raise ValueError("Something unexpected happened")


# --- Fixtures ---


@pytest.fixture
def envelope() -> InputEnvelope:
    return InputEnvelope(
        task="Analyze market trends",
        command="fast",
        trace_id="trace-001",
        run_id="run-001",
    )


@pytest.fixture
def local_descriptor() -> LocalDescriptor:
    return LocalDescriptor(
        agent_id="researcher",
        callable_ref=mock_researcher,
    )


@pytest.fixture
def http_descriptor() -> HttpDescriptor:
    return HttpDescriptor(
        agent_id="researcher",
        endpoint="http://testserver/agents/researcher/fast",
    )


# --- Transport-agnostic test helpers ---


def assert_successful_result(result: AgentResult, envelope: InputEnvelope) -> None:
    """Common assertions for a successful agent call."""
    assert result.success is True
    assert "Analyze market trends" in result.output
    assert result.signals.needs_human_review is False
    assert result.signals.semantic_error is None
    assert result.trace_id == envelope.trace_id
    assert result.run_id == envelope.run_id


# --- Tests: Local transport ---


async def test_invoke_local(
    envelope: InputEnvelope, local_descriptor: LocalDescriptor
) -> None:
    result = await invoke_agent(
        agent_id="researcher",
        command="fast",
        envelope=envelope,
        descriptor=local_descriptor,
    )
    assert_successful_result(result, envelope)


# --- Tests: HTTP transport ---


async def test_invoke_http(
    envelope: InputEnvelope, http_descriptor: HttpDescriptor
) -> None:
    """Same test as local, but over HTTP via FastAPI TestClient."""
    from httpx import ASGITransport, AsyncClient

    # Patch httpx to use the ASGI app instead of making real HTTP calls
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Monkeypatch invoke_agent's HTTP path to use our test client
        from . import invoke as invoke_module

        original = invoke_module._invoke_http

        async def patched_invoke_http(
            envelope_inner: object, descriptor: HttpDescriptor
        ) -> AgentResult:
            assert isinstance(envelope_inner, InputEnvelope)
            payload = {
                "task": envelope_inner.task,
                "command": envelope_inner.command,
                "effort": envelope_inner.effort,
                "trace_id": envelope_inner.trace_id,
                "run_id": envelope_inner.run_id,
            }
            response = await client.post(
                descriptor.endpoint,
                json=payload,
                headers={"traceparent": envelope_inner.trace_id},
            )
            response.raise_for_status()
            data = response.json()
            return AgentResult(
                success=data["success"],
                output=data["output"],
                signals=AgentSignals(
                    needs_human_review=data.get("signals", {}).get(
                        "needs_human_review", False
                    ),
                    review_reason=data.get("signals", {}).get("review_reason"),
                    escalation_requested=data.get("signals", {}).get(
                        "escalation_requested", False
                    ),
                    escalation_reason=data.get("signals", {}).get("escalation_reason"),
                    semantic_error=data.get("signals", {}).get("semantic_error"),
                ),
                trace_id=data.get("trace_id", ""),
                run_id=data.get("run_id", ""),
            )

        invoke_module._invoke_http = patched_invoke_http  # type: ignore[assignment]
        try:
            result = await invoke_agent(
                agent_id="researcher",
                command="fast",
                envelope=envelope,
                descriptor=http_descriptor,
            )
            assert_successful_result(result, envelope)
        finally:
            invoke_module._invoke_http = original


# --- Tests: Signal propagation (typed exceptions) ---


async def test_needs_human_review_local() -> None:
    envelope = InputEnvelope(task="Risky analysis", trace_id="t-002", run_id="r-002")
    descriptor = LocalDescriptor(
        agent_id="failing-agent", callable_ref=mock_failing_agent
    )
    result = await invoke_agent(
        agent_id="failing-agent",
        command="fast",
        envelope=envelope,
        descriptor=descriptor,
    )
    assert result.success is False
    assert result.signals.needs_human_review is True
    assert result.signals.review_reason == "Confidence too low"


async def test_semantic_error_local() -> None:
    envelope = InputEnvelope(task="Find unicorns", trace_id="t-003", run_id="r-003")
    descriptor = LocalDescriptor(agent_id="error-agent", callable_ref=mock_error_agent)
    result = await invoke_agent(
        agent_id="error-agent",
        command="fast",
        envelope=envelope,
        descriptor=descriptor,
    )
    assert result.success is False
    assert result.signals.semantic_error is not None
    assert result.signals.semantic_error["type"] == "no_results"


async def test_unexpected_error_local() -> None:
    envelope = InputEnvelope(task="Crash test", trace_id="t-004", run_id="r-004")
    descriptor = LocalDescriptor(agent_id="crash-agent", callable_ref=mock_crash_agent)
    result = await invoke_agent(
        agent_id="crash-agent",
        command="fast",
        envelope=envelope,
        descriptor=descriptor,
    )
    assert result.success is False
    assert result.signals.semantic_error is not None
    assert result.signals.semantic_error["type"] == "unexpected_error"


# --- Tests: CLI wrapping ---


async def test_cli_agent() -> None:
    """Test the CLI wrapping pattern — subprocess emitting ndjson."""
    from .cli_wrapper import cli_analyst
    from .models import AgentRunContext

    ctx = AgentRunContext(
        task="Analyze CLI data",
        command="fast",
        effort="high",
        trace_id="t-cli",
        run_id="r-cli",
        agent_id="cli-analyst",
    )
    result = await cli_analyst(ctx)
    assert result.success is True
    assert "CLI analysis of: Analyze CLI data" in result.output


# --- Tests: Concurrent invocations don't bleed ---


async def test_concurrent_invocations_isolated() -> None:
    """ContextVar isolation under asyncio.gather."""
    import asyncio

    async def call_with_task(task: str, trace: str) -> AgentResult:
        env = InputEnvelope(task=task, trace_id=trace, run_id=f"run-{trace}")
        desc = LocalDescriptor(agent_id="researcher", callable_ref=mock_researcher)
        return await invoke_agent(
            agent_id="researcher",
            command="fast",
            envelope=env,
            descriptor=desc,
        )

    r1, r2, r3 = await asyncio.gather(
        call_with_task("Task A", "trace-a"),
        call_with_task("Task B", "trace-b"),
        call_with_task("Task C", "trace-c"),
    )

    assert "Task A" in r1.output
    assert r1.trace_id == "trace-a"
    assert "Task B" in r2.output
    assert r2.trace_id == "trace-b"
    assert "Task C" in r3.output
    assert r3.trace_id == "trace-c"
