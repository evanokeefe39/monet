"""Low-level LangGraph SDK helpers — private implementation detail.

These wrap the ``langgraph_sdk`` client for thread management,
streaming, and state inspection.  ``MonetClient`` composes them;
external callers should not use these directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from langgraph_sdk.client import LangGraphClient

# ── Graph ID constants (server contract, not caller concern) ────────

TRACE_CARRIER_METADATA_KEY = "monet_trace_carrier"

# ── Metadata keys for thread tagging ────────────────────────────────

MONET_RUN_ID_KEY = "monet_run_id"
MONET_GRAPH_KEY = "monet_graph"
MONET_CHAT_NAME_KEY = "monet_chat_name"


# ── Client factory ──────────────────────────────────────────────────


def make_client(url: str = "http://localhost:2026") -> LangGraphClient:
    """Create a LangGraph SDK client pointed at *url*."""
    from langgraph_sdk import get_client

    return get_client(url=url)


async def create_thread(
    client: LangGraphClient,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Create a fresh server-side thread and return its id.

    Args:
        client: LangGraph SDK client.
        metadata: Optional metadata dict attached to the thread.
            Used by ``MonetClient`` to tag threads with ``monet_run_id``
            and ``monet_graph`` for later search.
    """
    kwargs: dict[str, Any] = {}
    if metadata:
        kwargs["metadata"] = metadata
    thread = await client.threads.create(**kwargs)
    return str(thread["thread_id"])


# ── Streaming ───────────────────────────────────────────────────────


async def stream_run(
    client: LangGraphClient,
    thread_id: str,
    graph_id: str,
    *,
    input: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Stream a run on *graph_id* and yield ``(mode, data)`` tuples.

    Pass either *input* (to start a new run) or *command* (to resume
    an interrupt).
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


# ── State helpers ───────────────────────────────────────────────────


async def get_state_values(
    client: LangGraphClient,
    thread_id: str,
) -> tuple[dict[str, Any], list[str]]:
    """Return ``(values, next_nodes)`` for the current thread state.

    *next_nodes* is empty when the run completed normally and
    contains interrupt node names when the run is paused.
    """
    state = await client.threads.get_state(thread_id)
    raw = state.get("values") or {}
    values: dict[str, Any] = raw if isinstance(raw, dict) else {}
    nxt = list(state.get("next") or [])
    return values, nxt


async def drain_stream(
    client: LangGraphClient,
    thread_id: str,
    graph_id: str,
    *,
    input: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
    trace_carrier: dict[str, str] | None = None,
    on_event: Callable[[str, Any], None] | None = None,
) -> None:
    """Stream a run to completion, optionally dispatching events.

    *on_event* receives ``(mode, data)`` for each chunk.
    """
    meta = {TRACE_CARRIER_METADATA_KEY: trace_carrier} if trace_carrier else None
    async for mode, data in stream_run(
        client,
        thread_id,
        graph_id,
        input=input,
        command=command,
        metadata=meta,
    ):
        if mode == "error":
            raise RuntimeError(f"server error: {data}")
        if on_event is not None:
            on_event(mode, data)


# ── State initializers ──────────────────────────────────────────────


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


def chat_input(message: str) -> dict[str, Any]:
    """Build the input state dict for the chat graph."""
    return {
        "messages": [{"role": "user", "content": message}],
    }
