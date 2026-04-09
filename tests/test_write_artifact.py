"""Tests for get_catalogue() and automatic content offload."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from monet import agent, get_catalogue
from monet._registry import default_registry  # internal: registry_scope fixture
from monet.catalogue import InMemoryCatalogueClient, configure_catalogue

if TYPE_CHECKING:
    from monet.types import AgentRunContext


def _ctx(**overrides: object) -> AgentRunContext:
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


@pytest.fixture(autouse=True)
def _catalogue() -> None:  # type: ignore[misc]
    configure_catalogue(InMemoryCatalogueClient())
    yield
    configure_catalogue(None)


# --- get_catalogue().write() tests ---


async def test_write_no_backend() -> None:
    """get_catalogue().write() raises without a backend."""
    configure_catalogue(None)
    handle = get_catalogue()
    with pytest.raises(NotImplementedError, match="catalogue backend"):
        await handle.write(
            content=b"test",
            content_type="text/plain",
            summary="x",
            confidence=0.5,
            completeness="complete",
        )


async def test_write_returns_pointer() -> None:
    """get_catalogue().write() writes and returns a pointer."""

    @agent(agent_id="artifact-writer")
    async def my_agent(task: str) -> str:
        ptr = await get_catalogue().write(
            content=b"Hello catalogue",
            content_type="text/plain",
            summary="Test artifact",
            confidence=0.9,
            completeness="complete",
        )
        return f"wrote: {ptr['artifact_id']}"

    ctx = _ctx(task="test", trace_id="t-1", run_id="r-1", agent_id="artifact-writer")
    result = await my_agent(ctx)
    assert result.success is True
    assert "wrote:" in result.output


async def test_artifacts_collected_in_result() -> None:
    """Artifacts written by an agent are collected in result.artifacts."""

    @agent(agent_id="collector-agent")
    async def collector(task: str) -> str:
        await get_catalogue().write(
            content=b"a",
            content_type="text/plain",
            summary="first",
            confidence=0.8,
            completeness="complete",
        )
        await get_catalogue().write(
            content=b"b",
            content_type="text/plain",
            summary="second",
            confidence=0.8,
            completeness="complete",
        )
        return "done"

    ctx = _ctx(task="x", agent_id="collector-agent")
    result = await collector(ctx)
    assert result.success is True
    assert len(result.artifacts) == 2


async def test_read_no_side_effects() -> None:
    """get_catalogue().read() does not register with the artifact collector."""

    @agent(agent_id="reader-agent")
    async def reader(task: str) -> str:
        # write one artifact (collected)
        ptr = await get_catalogue().write(
            content=b"hello",
            content_type="text/plain",
            summary="written",
            confidence=0.8,
            completeness="complete",
        )
        # read it back (must NOT add another entry to artifacts)
        content, _meta = await get_catalogue().read(ptr["artifact_id"])
        return content.decode()

    ctx = _ctx(task="x", agent_id="reader-agent")
    result = await reader(ctx)
    assert result.success is True
    assert result.output == "hello"
    assert len(result.artifacts) == 1  # only the write registered, not the read


# --- Content offload tests ---


async def test_content_offload_small_output() -> None:
    """Small outputs stay inline, no offload."""

    @agent(agent_id="small-agent")
    async def small_agent(task: str) -> str:
        return "short result"

    ctx = _ctx(task="test", agent_id="small-agent")
    result = await small_agent(ctx)
    assert result.success is True
    assert isinstance(result.output, str)
    assert result.output == "short result"
    assert result.artifacts == ()


async def test_content_offload_large_output() -> None:
    """Large outputs are offloaded to catalogue automatically."""

    @agent(agent_id="large-agent")
    async def large_agent(task: str) -> str:
        return "x" * 5000  # Exceeds DEFAULT_CONTENT_LIMIT (4000)

    ctx = _ctx(
        task="test",
        trace_id="t-large",
        run_id="r-large",
        agent_id="large-agent",
    )
    result = await large_agent(ctx)
    assert result.success is True
    # output is now an inline summary string; the full content lives in artifacts
    assert isinstance(result.output, str)
    assert len(result.output) <= 200
    assert len(result.artifacts) == 1


async def test_content_offload_no_catalogue() -> None:
    """Large output without catalogue stays as string (no offload)."""
    configure_catalogue(None)

    @agent(agent_id="no-cat-agent")
    async def no_cat_agent(task: str) -> str:
        return "y" * 5000

    ctx = _ctx(task="test", agent_id="no-cat-agent")
    result = await no_cat_agent(ctx)
    assert result.success is True
    assert isinstance(result.output, str)
    assert len(result.output) == 5000


# --- Signal accumulation through decorator ---


async def test_signal_accumulation_through_decorator() -> None:
    """Validates ContextVar wiring + signal accumulation + decorator assembly."""
    from monet import emit_signal
    from monet.types import Signal, SignalType

    @agent(agent_id="signal-agent")
    async def signal_agent(task: str) -> str:
        sig: Signal = {
            "type": SignalType.LOW_CONFIDENCE,
            "reason": "uncertain",
            "metadata": None,
        }
        emit_signal(sig)
        return "done"

    ctx = _ctx(task="x", agent_id="signal-agent")
    result = await signal_agent(ctx)
    assert result.success is True
    assert result.has_signal(SignalType.LOW_CONFIDENCE) is True
