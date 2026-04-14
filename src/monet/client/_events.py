"""Typed events yielded by ``MonetClient.run()`` and query responses.

These events are *graph-agnostic* — any LangGraph graph driven through
:class:`~monet.client.MonetClient` produces them. Pipeline-specific
events (e.g. ``TriageComplete``, ``PlanReady``, ``WaveComplete``) live
next to their pipeline adapter — see ``monet.pipelines.default.events``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Run stream events ───────────────────────────────────────────────


@dataclass(frozen=True)
class RunStarted:
    """A new run has been created on a thread."""

    run_id: str
    graph_id: str
    thread_id: str


@dataclass(frozen=True)
class NodeUpdate:
    """A LangGraph node wrote a state delta.

    ``update`` is the raw dict the node returned — its shape is the
    node's contract, not ours.
    """

    run_id: str
    node: str
    update: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentProgress:
    """Streaming progress from an agent invocation (``emit_progress``)."""

    run_id: str
    agent_id: str
    status: str
    reasons: str = ""


@dataclass(frozen=True)
class SignalEmitted:
    """A monet agent emitted a signal via ``emit_signal``."""

    run_id: str
    agent_id: str
    signal_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Interrupt:
    """The graph hit ``interrupt(...)`` — run is paused.

    ``tag`` is the interrupt node's name (``next_nodes[0]`` when a
    single node is pending). ``values`` is the dict the node passed to
    ``interrupt()``. Resume via :meth:`MonetClient.resume` with the
    matching ``tag`` and a payload the node will accept.
    """

    run_id: str
    tag: str
    values: dict[str, Any] = field(default_factory=dict)
    next_nodes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunComplete:
    """Run finished successfully."""

    run_id: str
    final_values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunFailed:
    """Run terminated with an error."""

    run_id: str
    error: str


RunEvent = (
    RunStarted
    | NodeUpdate
    | AgentProgress
    | SignalEmitted
    | Interrupt
    | RunComplete
    | RunFailed
)
"""Union of all event types yielded by ``MonetClient.run()``."""


# ── Query response types ────────────────────────────────────────────


@dataclass(frozen=True)
class RunSummary:
    """Lightweight run record returned by :meth:`MonetClient.list_runs`."""

    run_id: str
    status: str
    completed_stages: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass(frozen=True)
class RunDetail:
    """Generic run snapshot — any pipeline, any graph topology.

    ``completed_stages`` is a timeline of per-graph stage names that
    have been observed for this run (derived from the graph IDs of its
    threads). ``values`` is a merge of each thread's state values.
    Pipeline-specific typed views (e.g. ``DefaultPipelineRunDetail``)
    project from this.
    """

    run_id: str
    status: str
    completed_stages: list[str] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)
    pending_interrupt: Interrupt | None = None


@dataclass(frozen=True)
class PendingDecision:
    """A run waiting for human input, returned by ``list_pending``.

    ``decision_type`` is the raw interrupt tag (the node name that
    called ``interrupt()``). Pipeline adapters map tags to friendlier
    summaries for UI rendering.
    """

    run_id: str
    decision_type: str
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatSummary:
    """Lightweight chat session record returned by ``list_chats``."""

    thread_id: str
    name: str
    message_count: int
    created_at: str = ""
    updated_at: str = ""
