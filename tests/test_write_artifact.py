"""Tests for write_artifact() and automatic content offload."""

from __future__ import annotations

import pytest

from monet._decorator import agent
from monet._registry import default_registry
from monet._stubs import set_catalogue_client, write_artifact
from monet._types import AgentRunContext, ArtifactPointer
from monet.catalogue._memory import InMemoryCatalogueClient


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    with default_registry.registry_scope():
        yield


# --- write_artifact() tests ---


def test_write_artifact_no_client() -> None:
    """write_artifact() raises RuntimeError without a client."""
    from monet._stubs import _catalogue_client

    token = _catalogue_client.set(None)
    try:
        with pytest.raises(RuntimeError, match="No catalogue client"):
            write_artifact(b"test", "text/plain")
    finally:
        _catalogue_client.reset(token)


async def test_write_artifact_with_client() -> None:
    """write_artifact() writes to the catalogue and returns a pointer."""
    client = InMemoryCatalogueClient()
    set_catalogue_client(client)

    @agent(agent_id="artifact-writer")
    async def my_agent(task: str) -> str:
        ptr = write_artifact(
            b"Hello catalogue",
            "text/plain",
            summary="Test artifact",
            confidence=0.9,
        )
        return f"wrote: {ptr.artifact_id}"

    ctx = AgentRunContext(
        task="test",
        trace_id="t-1",
        run_id="r-1",
        agent_id="artifact-writer",
    )
    result = await my_agent(ctx)
    assert result.success is True
    assert "wrote:" in result.output

    # Verify the artifact is in the catalogue
    from monet._stubs import _catalogue_client

    _catalogue_client.set(None)


async def test_write_artifact_populates_metadata() -> None:
    """write_artifact() populates metadata from AgentRunContext."""
    client = InMemoryCatalogueClient()
    set_catalogue_client(client)

    @agent(agent_id="meta-agent", command="deep")
    async def meta_agent(task: str) -> str:
        ptr = write_artifact(
            b"metadata test",
            "application/json",
            summary="Meta test",
        )
        _content, meta = client.read(ptr.artifact_id)
        assert meta.trace_id == "t-meta"
        assert meta.run_id == "r-meta"
        assert meta.invocation_command == "deep"
        assert meta.created_by == "meta-agent"
        return "done"

    ctx = AgentRunContext(
        task="test",
        command="deep",
        trace_id="t-meta",
        run_id="r-meta",
        agent_id="meta-agent",
    )
    result = await meta_agent(ctx)
    assert result.success is True

    from monet._stubs import _catalogue_client

    _catalogue_client.set(None)


# --- Content offload tests ---


async def test_content_offload_small_output() -> None:
    """Small outputs stay inline, no offload."""
    client = InMemoryCatalogueClient()
    set_catalogue_client(client)

    @agent(agent_id="small-agent")
    async def small_agent(task: str) -> str:
        return "short result"

    ctx = AgentRunContext(task="test", agent_id="small-agent")
    result = await small_agent(ctx)
    assert result.success is True
    assert isinstance(result.output, str)
    assert result.output == "short result"
    assert result.artifacts == []

    from monet._stubs import _catalogue_client

    _catalogue_client.set(None)


async def test_content_offload_large_output() -> None:
    """Large outputs are offloaded to catalogue automatically."""
    client = InMemoryCatalogueClient()
    set_catalogue_client(client)

    @agent(agent_id="large-agent")
    async def large_agent(task: str) -> str:
        return "x" * 5000  # Exceeds DEFAULT_CONTENT_LIMIT (4000)

    ctx = AgentRunContext(
        task="test",
        trace_id="t-large",
        run_id="r-large",
        agent_id="large-agent",
    )
    result = await large_agent(ctx)
    assert result.success is True
    assert isinstance(result.output, ArtifactPointer)
    assert len(result.artifacts) == 1
    assert result.artifacts[0].artifact_id == result.output.artifact_id

    from monet._stubs import _catalogue_client

    _catalogue_client.set(None)


async def test_content_offload_no_catalogue() -> None:
    """Large output without catalogue stays as string (no offload)."""
    from monet._stubs import _catalogue_client

    _catalogue_client.set(None)

    @agent(agent_id="no-cat-agent")
    async def no_cat_agent(task: str) -> str:
        return "y" * 5000

    ctx = AgentRunContext(task="test", agent_id="no-cat-agent")
    result = await no_cat_agent(ctx)
    assert result.success is True
    assert isinstance(result.output, str)
    assert len(result.output) == 5000
