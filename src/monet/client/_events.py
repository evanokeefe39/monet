"""Typed events yielded by ``MonetClient.run()`` and query responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from monet.types import ArtifactPointer

# ── Run stream events ───────────────────────────────────────────────


@dataclass(frozen=True)
class TriageComplete:
    """Triage phase finished — topic classified."""

    run_id: str
    complexity: str
    suggested_agents: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlanReady:
    """Planner produced a routing skeleton (flat DAG).

    ``nodes`` items are dumps of ``RoutingNode`` — each has
    ``{id, agent_id, command, depends_on}``.
    """

    run_id: str
    goal: str
    nodes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PlanApproved:
    """Plan was approved (auto or manual)."""

    run_id: str


@dataclass(frozen=True)
class PlanInterrupt:
    """Run paused — plan needs human approval.

    Use ``client.approve_plan``, ``client.revise_plan``, or
    ``client.reject_plan`` to continue. Carries the skeleton directly so
    UIs can render plan structure without a catalogue read.
    """

    run_id: str
    goal: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentProgress:
    """Streaming progress from an agent invocation."""

    run_id: str
    agent_id: str
    status: str
    reasons: str = ""


@dataclass(frozen=True)
class WaveComplete:
    """A dispatch batch of parallel agent invocations finished.

    ``wave_index`` is a monotonic counter assigned client-side for ordering;
    it no longer corresponds to a planning phase (the flat DAG has no
    phases). ``node_ids`` are the routing-skeleton node ids that completed
    in this batch.
    """

    run_id: str
    wave_index: int
    node_ids: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ReflectionComplete:
    """QA reflection on a wave finished."""

    run_id: str
    verdict: str
    notes: str = ""


@dataclass(frozen=True)
class ExecutionInterrupt:
    """Run paused — execution needs human decision.

    Use ``client.retry_wave`` or ``client.abort_run`` to continue.
    ``pending_node_ids`` are routing-skeleton nodes that have not yet
    completed successfully.
    """

    run_id: str
    reason: str
    pending_node_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunComplete:
    """Run finished successfully."""

    run_id: str
    wave_results: list[dict[str, Any]] = field(default_factory=list)
    wave_reflections: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RunFailed:
    """Run terminated with an error."""

    run_id: str
    error: str


RunEvent = (
    TriageComplete
    | PlanReady
    | PlanApproved
    | PlanInterrupt
    | AgentProgress
    | WaveComplete
    | ReflectionComplete
    | ExecutionInterrupt
    | RunComplete
    | RunFailed
)
"""Union of all event types yielded by ``MonetClient.run()``."""


# ── Query response types ────────────────────────────────────────────


@dataclass(frozen=True)
class RunSummary:
    """Lightweight run record returned by ``list_runs``."""

    run_id: str
    status: str
    phase: str
    created_at: str = ""


@dataclass(frozen=True)
class RunDetail:
    """Full run state returned by ``get_run`` and ``get_results``.

    ``routing_skeleton`` is the planner's flat DAG (``{goal, nodes}``);
    ``work_brief_pointer`` is the catalogue pointer to the full brief,
    resolvable via ``monet.core.context_resolver.resolve_context``.
    """

    run_id: str
    status: str
    phase: str
    triage: dict[str, Any] = field(default_factory=dict)
    routing_skeleton: dict[str, Any] = field(default_factory=dict)
    work_brief_pointer: ArtifactPointer | None = None
    wave_results: list[dict[str, Any]] = field(default_factory=list)
    wave_reflections: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PendingDecision:
    """A run waiting for human input, returned by ``list_pending``."""

    run_id: str
    decision_type: str  # "plan_approval" | "execution_review"
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
