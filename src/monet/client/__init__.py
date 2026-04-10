"""Client SDK utilities for interacting with monet graphs.

Provides helpers for LangGraph SDK client operations: streaming,
state inspection, thread management, and state initialization.
Extracted from the patterns in ``examples/social_media_llm/``.

Usage::

    from monet.client import make_client, create_thread, stream_run

    client = make_client("http://localhost:2024")
    thread_id = await create_thread(client)
    async for mode, data in stream_run(client, thread_id, "entry", input={...}):
        print(mode, data)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from langgraph_sdk.client import LangGraphClient

__all__ = [
    "ENTRY_GRAPH",
    "EXECUTION_GRAPH",
    "PLANNING_GRAPH",
    "create_thread",
    "drain_stream",
    "entry_input",
    "execution_input",
    "get_state_values",
    "make_client",
    "planning_input",
    "stream_run",
]

# ── Graph ID constants ───────────────────────────────────────────────

ENTRY_GRAPH = "entry"
PLANNING_GRAPH = "planning"
EXECUTION_GRAPH = "execution"


# ── Client factory ───────────────────────────────────────────────────


def make_client(url: str = "http://localhost:2024") -> LangGraphClient:
    """Create a LangGraph SDK client pointed at ``url``."""
    from langgraph_sdk import get_client

    return get_client(url=url)


async def create_thread(client: LangGraphClient) -> str:
    """Create a fresh server-side thread and return its id."""
    thread = await client.threads.create()
    return str(thread["thread_id"])


# ── Streaming ────────────────────────────────────────────────────────


async def stream_run(
    client: LangGraphClient,
    thread_id: str,
    graph_id: str,
    *,
    input: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Stream a run on ``graph_id`` and yield ``(mode, data)`` tuples.

    Pass either ``input`` (to start a new run) or ``command`` (to resume
    an interrupt). The SDK's ``client.runs.stream(...)`` returns
    ``StreamPart`` objects; we collapse them to ``(mode, data)`` so
    callers don't need to know about ``StreamPart``.
    """
    kwargs: dict[str, Any] = {"stream_mode": ["updates", "custom"]}
    if command is not None:
        kwargs["command"] = command
    else:
        kwargs["input"] = input or {}
    if metadata:
        kwargs["metadata"] = metadata

    async for chunk in client.runs.stream(thread_id, graph_id, **kwargs):
        event = getattr(chunk, "event", None) or ""
        data = getattr(chunk, "data", None)
        if event.startswith("updates"):
            yield ("updates", data)
        elif event.startswith("custom"):
            yield ("custom", data)
        elif event.startswith("error"):
            yield ("error", data)


# ── State helpers ────────────────────────────────────────────────────


async def get_state_values(
    client: LangGraphClient,
    thread_id: str,
) -> tuple[dict[str, Any], list[str]]:
    """Return ``(values, next_nodes)`` for the current thread state.

    ``next_nodes`` is empty when the run completed normally and
    contains interrupt node names when the run is paused.
    """
    state = await client.threads.get_state(thread_id)
    values = state.get("values") or {}
    nxt = list(state.get("next") or [])
    return values, nxt


async def drain_stream(
    client: LangGraphClient,
    thread_id: str,
    graph_id: str,
    label: str,
    *,
    input: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
    trace_carrier: dict[str, str] | None = None,
    on_event: Callable[[str, str, Any], None] | None = None,
) -> None:
    """Stream a run to completion, optionally dispatching events.

    ``on_event`` receives ``(label, mode, data)`` for each chunk.
    If not provided, events are silently consumed.

    ``trace_carrier`` is a W3C traceparent carrier dict passed as
    run metadata for distributed tracing correlation.
    """
    from monet.tracing import TRACE_CARRIER_METADATA_KEY

    metadata = {TRACE_CARRIER_METADATA_KEY: trace_carrier} if trace_carrier else None
    async for mode, data in stream_run(
        client,
        thread_id,
        graph_id,
        input=input,
        command=command,
        metadata=metadata,
    ):
        if mode == "error":
            raise RuntimeError(f"server error during {label}: {data}")
        if on_event is not None:
            on_event(label, mode, data)


# ── State initializers ───────────────────────────────────────────────


def entry_input(task: str, run_id: str) -> dict[str, Any]:
    """Build the initial state dict for the entry (triage) graph."""
    return {
        "task": task,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
    }


def planning_input(task: str, run_id: str) -> dict[str, Any]:
    """Build the initial state dict for the planning graph."""
    return {
        "task": task,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
        "revision_count": 0,
    }


def execution_input(work_brief: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Build the initial state dict for the execution graph."""
    return {
        "work_brief": work_brief,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
        "current_phase_index": 0,
        "current_wave_index": 0,
        "wave_results": [],
        "wave_reflections": [],
        "completed_phases": [],
        "revision_count": 0,
    }
