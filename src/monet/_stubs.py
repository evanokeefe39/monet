"""SDK utility functions backed by infrastructure.

write_artifact() calls the catalogue client from context.
emit_progress() emits to LangGraph's stream writer.
emit_signal() accumulates non-fatal signals during agent execution.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Literal

from ._context import get_run_context

if TYPE_CHECKING:
    from ._types import ArtifactPointer, Signal
    from .catalogue._protocol import CatalogueClient

_catalogue_client: ContextVar[CatalogueClient | None] = ContextVar(
    "_catalogue_client", default=None
)


def set_catalogue_client(client: CatalogueClient) -> None:
    """Set the catalogue client for the current context.

    Called at server startup or in test fixtures.
    """
    _catalogue_client.set(client)


def get_catalogue_client() -> CatalogueClient | None:
    """Get the current catalogue client, or None if not configured."""
    return _catalogue_client.get(None)


async def write_artifact(
    content: bytes,
    content_type: str,
    summary: str = "",
    confidence: float = 0.0,
    completeness: Literal["complete", "partial", "resource-bounded"] = "complete",
    sensitivity_label: Literal[
        "public", "internal", "confidential", "restricted"
    ] = "internal",
    **kwargs: Any,
) -> ArtifactPointer:
    """Write an artifact to the catalogue.

    Preconditions:
        Must be called inside a decorated agent function.
        A catalogue client must be configured via set_catalogue_client().
    Postconditions:
        Returns an ArtifactPointer with the artifact ID and URL.
    """
    client = _catalogue_client.get(None)
    if client is None:
        msg = (
            "No catalogue client configured. "
            "Call set_catalogue_client() at startup or in test fixtures."
        )
        raise RuntimeError(msg)

    from .catalogue._metadata import ArtifactMetadata

    ctx = get_run_context()
    metadata = ArtifactMetadata(
        content_type=content_type,
        summary=summary,
        created_by=ctx.agent_id or "unknown",
        trace_id=ctx.trace_id,
        run_id=ctx.run_id,
        invocation_command=ctx.command,
        invocation_effort=ctx.effort,
        confidence=confidence,
        completeness=completeness,
        sensitivity_label=sensitivity_label,
        **kwargs,
    )
    return client.write(content, metadata)


def emit_progress(data: dict[str, Any]) -> None:
    """Emit a progress event for intra-node streaming.

    Calls LangGraph's get_stream_writer() to emit custom events
    that appear in astream output with stream_mode=["custom"].
    No-op outside the LangGraph execution context.
    """
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        writer(data)
    except Exception:
        pass


# --- Signal accumulation ---

_signal_collector: ContextVar[list[Signal] | None] = ContextVar(
    "_signal_collector", default=None
)


def emit_signal(signal: Signal) -> None:
    """Emit a non-fatal signal to the orchestrator.

    Signals accumulate — multiple can be true simultaneously.
    The agent continues execution and can return a result alongside signals.
    No-op outside the @agent decorator context.

    For fatal conditions where the agent cannot continue, raise
    NeedsHumanReview, EscalationRequired, or SemanticError instead.
    """
    collector = _signal_collector.get()
    if collector is not None:
        collector.append(signal)
