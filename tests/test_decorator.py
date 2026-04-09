"""Tests for the @agent decorator."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from monet import EscalationRequired, NeedsHumanReview, SemanticError, agent
from monet._manifest import default_manifest
from monet._registry import (
    default_registry,  # internal: needed for registry_scope test fixture
)
from monet.catalogue import InMemoryCatalogueClient, configure_catalogue

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
    """Isolate each test from registry side effects."""
    with default_registry.registry_scope(), default_manifest.manifest_scope():
        yield


# --- Basic decoration and invocation ---


async def test_minimal_agent() -> None:
    @agent(agent_id="test-minimal")
    async def my_agent(task: str) -> str:
        return f"Done: {task}"

    ctx = _ctx(task="hello", trace_id="t1", run_id="r1", agent_id="test-minimal")
    result = await my_agent(ctx)
    assert result.success is True
    assert result.output == "Done: hello"
    assert result.trace_id == "t1"
    assert result.run_id == "r1"


async def test_sync_function() -> None:
    @agent(agent_id="test-sync")
    def sync_agent(task: str) -> str:
        return f"Sync: {task}"

    ctx = _ctx(task="test", agent_id="test-sync")
    result = await sync_agent(ctx)
    assert result.success is True
    assert result.output == "Sync: test"


# --- Parameter injection ---


async def test_injects_only_declared_params() -> None:
    @agent(agent_id="test-partial")
    async def partial_agent(task: str, command: str) -> str:
        return f"{command}: {task}"

    ctx = _ctx(task="analyze", command="deep", agent_id="test-partial")
    result = await partial_agent(ctx)
    assert result.output == "deep: analyze"


async def test_injects_all_fields() -> None:
    @agent(agent_id="test-all")
    async def all_fields_agent(
        task: str,
        context: list,  # type: ignore[type-arg]
        command: str,
        trace_id: str,
        run_id: str,
        agent_id: str,
        skills: list,  # type: ignore[type-arg]
    ) -> str:
        return f"{agent_id}/{command}"

    ctx = _ctx(
        task="t",
        command="deep",
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
    # Empty agent_id via verbose form raises ValueError at decoration.
    with pytest.raises(ValueError, match="agent_id is required"):

        @agent(agent_id="")
        async def bad(task: str) -> str:
            return "x"


def test_dual_call_signature() -> None:
    """agent("name") returns a partial that registers commands."""
    researcher = agent("dual-test")

    @researcher(command="fast")
    async def fast_handler(task: str) -> str:
        return f"fast:{task}"

    from monet._registry import default_registry

    assert default_registry.lookup("dual-test", "fast") is not None


# --- Typed exception -> signals ---


async def test_needs_human_review_signal() -> None:
    @agent(agent_id="test-review")
    async def review_agent(task: str) -> str:
        raise NeedsHumanReview(reason="Low confidence")

    ctx = _ctx(task="x", agent_id="test-review")
    result = await review_agent(ctx)
    assert result.success is False
    assert len(result.signals) == 1
    assert result.signals[0]["type"] == "needs_human_review"
    assert result.signals[0]["reason"] == "Low confidence"


async def test_escalation_signal() -> None:
    @agent(agent_id="test-escalate")
    async def escalation_agent(task: str) -> str:
        raise EscalationRequired(reason="Needs admin")

    ctx = _ctx(task="x", agent_id="test-escalate")
    result = await escalation_agent(ctx)
    assert result.success is False
    assert len(result.signals) == 1
    assert result.signals[0]["type"] == "escalation_required"
    assert result.signals[0]["reason"] == "Needs admin"


async def test_semantic_error_signal() -> None:
    @agent(agent_id="test-semantic")
    async def semantic_agent(task: str) -> str:
        raise SemanticError(type="no_results", message="Empty search")

    ctx = _ctx(task="x", agent_id="test-semantic")
    result = await semantic_agent(ctx)
    assert result.success is False
    assert len(result.signals) == 1
    assert result.signals[0]["type"] == "semantic_error"
    assert result.signals[0]["reason"] == "Empty search"
    assert result.signals[0]["metadata"] == {"error_type": "no_results"}


async def test_unexpected_error_wrapped() -> None:
    @agent(agent_id="test-crash")
    async def crash_agent(task: str) -> str:
        raise ValueError("boom")

    ctx = _ctx(task="x", agent_id="test-crash")
    result = await crash_agent(ctx)
    assert result.success is False
    assert len(result.signals) == 1
    assert result.signals[0]["type"] == "semantic_error"
    assert result.signals[0]["metadata"] == {"error_type": "unexpected_error"}
    assert "boom" in result.signals[0]["reason"]


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
        concurrent_agent(_ctx(task="A", trace_id="t-a", agent_id="test-concurrent")),
        concurrent_agent(_ctx(task="B", trace_id="t-b", agent_id="test-concurrent")),
        concurrent_agent(_ctx(task="C", trace_id="t-c", agent_id="test-concurrent")),
    )

    assert results[0].output == "A|t-a"
    assert results[0].trace_id == "t-a"
    assert results[1].output == "B|t-b"
    assert results[1].trace_id == "t-b"
    assert results[2].output == "C|t-c"
    assert results[2].trace_id == "t-c"


# --- Auto-offload and double-write dedupe ---


@pytest.fixture
def _catalogue():  # type: ignore[no-untyped-def]
    configure_catalogue(InMemoryCatalogueClient())
    yield
    configure_catalogue(None)


async def test_auto_offload_naive_agent(_catalogue: None) -> None:
    """Agent returns >limit bytes, writes nothing — auto-offload kicks in."""
    big = "x" * 5000

    @agent(agent_id="test-offload-naive")
    async def naive_agent(task: str) -> str:
        return big

    result = await naive_agent(_ctx(task="t", agent_id="test-offload-naive"))
    assert len(result.artifacts) == 1
    assert result.output == big[:200]


async def test_double_write_dedupes(_catalogue: None) -> None:
    """Agent writes bytes AND returns the same bytes — one artifact, not two."""
    big = "y" * 5000

    @agent(agent_id="test-offload-dedupe")
    async def dedupe_agent(task: str) -> str:
        from monet import get_catalogue

        await get_catalogue().write(
            content=big.encode(),
            content_type="text/markdown",
            summary=big[:200],
            confidence=0.9,
            completeness="complete",
        )
        return big

    result = await dedupe_agent(_ctx(task="t", agent_id="test-offload-dedupe"))
    assert len(result.artifacts) == 1
    assert result.output == big[:200]


async def test_side_artifact_still_offloads(_catalogue: None) -> None:
    """Agent writes a *different* big string as a side artifact while
    returning its own big string — both must land as artifacts."""
    side = "a" * 5000
    returned = "b" * 5000

    @agent(agent_id="test-offload-side")
    async def side_agent(task: str) -> str:
        from monet import get_catalogue

        await get_catalogue().write(
            content=side.encode(),
            content_type="text/markdown",
            summary="side outline",
            confidence=0.9,
            completeness="complete",
        )
        return returned

    result = await side_agent(_ctx(task="t", agent_id="test-offload-side"))
    assert len(result.artifacts) == 2
    assert result.output == returned[:200]


async def test_empty_string_no_artifacts_is_defect(_catalogue: None) -> None:
    """Poka-yoke: empty string return + zero artifacts is a defect signal,
    not a silent success. This is the regression guard for the BlockingError
    case where reference agents produced empty AgentResults for three
    revisions before the execution graph gave up."""

    @agent(agent_id="test-empty-string")
    async def empty_agent(task: str) -> str:
        return ""

    result = await empty_agent(_ctx(task="t", agent_id="test-empty-string"))
    assert result.success is False
    assert len(result.artifacts) == 0
    assert any(
        s["type"] == "semantic_error"
        and (s["metadata"] or {}).get("error_type") == "empty_agent_result"
        for s in result.signals
    )


async def test_none_return_no_artifacts_is_defect(_catalogue: None) -> None:
    """None return with no artifacts is the same defect class as empty
    string — agent produced nothing at all."""

    @agent(agent_id="test-none-return")
    async def none_agent(task: str) -> None:
        return None

    result = await none_agent(_ctx(task="t", agent_id="test-none-return"))
    assert result.success is False
    assert any(
        (s["metadata"] or {}).get("error_type") == "empty_agent_result"
        for s in result.signals
    )


async def test_allow_empty_opt_out(_catalogue: None) -> None:
    """Agents that legitimately return nothing (e.g. signal-only ack
    handlers) can opt out of the empty-result guard."""

    @agent(agent_id="test-allow-empty", allow_empty=True)
    async def ack_agent(task: str) -> None:
        return None

    result = await ack_agent(_ctx(task="t", agent_id="test-allow-empty"))
    assert result.success is True
    assert result.output is None
    assert not any(
        (s["metadata"] or {}).get("error_type") == "empty_agent_result"
        for s in result.signals
    )


async def test_dict_return_exempt_from_empty_guard(_catalogue: None) -> None:
    """Dict returns are structured output and exempt from the guard even
    when small."""

    @agent(agent_id="test-dict-return")
    async def dict_agent(task: str) -> dict:  # type: ignore[type-arg]
        return {"verdict": "pass"}

    result = await dict_agent(_ctx(task="t", agent_id="test-dict-return"))
    assert result.success is True
    assert result.output == {"verdict": "pass"}


async def test_empty_string_with_artifact_is_success(_catalogue: None) -> None:
    """A short/empty return string is fine as long as an artifact was
    written — the agent produced something, the inline output just
    wasn't the primary vehicle."""

    @agent(agent_id="test-empty-string-with-artifact")
    async def stringy_agent(task: str) -> str:
        from monet import get_catalogue

        await get_catalogue().write(
            content=b"real content",
            content_type="text/plain",
            summary="s",
            confidence=0.9,
            completeness="complete",
        )
        return ""

    result = await stringy_agent(
        _ctx(task="t", agent_id="test-empty-string-with-artifact")
    )
    assert result.success is True
    assert len(result.artifacts) == 1


async def test_short_return_with_explicit_write(_catalogue: None) -> None:
    """Short return + explicit write — 1 artifact, output unchanged."""

    @agent(agent_id="test-offload-short")
    async def short_agent(task: str) -> str:
        from monet import get_catalogue

        await get_catalogue().write(
            content=b"some artifact bytes",
            content_type="text/plain",
            summary="s",
            confidence=0.9,
            completeness="complete",
        )
        return "short reply"

    result = await short_agent(_ctx(task="t", agent_id="test-offload-short"))
    assert len(result.artifacts) == 1
    assert result.output == "short reply"
