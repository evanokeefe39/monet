"""Default pipeline event types and typed interrupt/resume payloads.

Lives outside the core :mod:`monet.client._events` so the client stays
graph-agnostic. The adapter at :func:`monet.pipelines.default.run`
consumes core events and yields these projected ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    from typing import NotRequired

    from monet.client._events import Interrupt, RunDetail
    from monet.types import ArtifactPointer


# ── Interrupt tag + resume payload contracts ────────────────────────

DefaultInterruptTag = Literal["human_approval", "human_interrupt"]
"""Interrupt node names used by the default pipeline graphs."""


class PlanApprovalPayload(TypedDict, total=False):
    """Resume payload for ``human_approval`` interrupts."""

    approved: bool
    feedback: NotRequired[str | None]


class ExecutionReviewPayload(TypedDict, total=False):
    """Resume payload for ``human_interrupt`` interrupts."""

    action: NotRequired[Literal["retry", "abort"] | None]


class PlanInterruptValues(TypedDict, total=False):
    """Shape of :attr:`Interrupt.values` emitted by ``human_approval``.

    Matches the ``interrupt()`` call at ``planning_graph.py:141``.
    """

    work_brief_pointer: ArtifactPointer
    routing_skeleton: dict[str, Any]


class ExecutionInterruptValues(TypedDict, total=False):
    """Shape of :attr:`Interrupt.values` emitted by ``human_interrupt``.

    Matches the ``interrupt()`` call at ``execution_graph.py:275``.
    """

    reason: str
    last_result: dict[str, Any]
    pending_node_ids: list[str]


# ── Pipeline event types ────────────────────────────────────────────


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

    Carries the work brief pointer and routing skeleton directly so UIs
    can render plan structure without an artifact read.
    """

    run_id: str
    work_brief_pointer: Any = None
    routing_skeleton: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WaveComplete:
    """A dispatch batch of parallel agent invocations finished.

    ``wave_index`` is a monotonic counter assigned by the adapter for
    ordering; it does not correspond to a planning phase (the flat DAG
    has no phases). ``node_ids`` are the routing-skeleton node ids that
    completed in this batch.
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

    Carries the shape that ``execution_graph.human_interrupt`` passes
    to ``interrupt()``.
    """

    run_id: str
    reason: str
    last_result: dict[str, Any] = field(default_factory=dict)
    pending_node_ids: list[str] = field(default_factory=list)


DefaultPipelineEvent = (
    TriageComplete
    | PlanReady
    | PlanApproved
    | PlanInterrupt
    | WaveComplete
    | ReflectionComplete
    | ExecutionInterrupt
)
"""Union of domain events yielded by the default pipeline adapter."""


# ── Typed RunDetail view ────────────────────────────────────────────


@dataclass(frozen=True)
class DefaultPipelineRunDetail:
    """Typed view over a generic :class:`RunDetail` for default-pipeline runs.

    Callers that know they're inspecting a default-pipeline run can
    read typed fields (``triage``, ``routing_skeleton``, ``work_brief_pointer``,
    ``wave_results``, ``wave_reflections``) instead of dict-key lookups
    on ``RunDetail.values``.
    """

    run_id: str
    status: str
    completed_stages: list[str]
    triage: dict[str, Any]
    routing_skeleton: dict[str, Any]
    work_brief_pointer: Any | None
    wave_results: list[dict[str, Any]]
    wave_reflections: list[dict[str, Any]]
    pending_interrupt: Interrupt | None

    @classmethod
    def from_run_detail(cls, detail: RunDetail) -> DefaultPipelineRunDetail:
        """Project a generic :class:`RunDetail` into the default-pipeline view."""
        values = detail.values
        return cls(
            run_id=detail.run_id,
            status=detail.status,
            completed_stages=list(detail.completed_stages),
            triage=values.get("triage") or {},
            routing_skeleton=values.get("routing_skeleton") or {},
            work_brief_pointer=values.get("work_brief_pointer"),
            wave_results=values.get("wave_results") or [],
            wave_reflections=values.get("wave_reflections") or [],
            pending_interrupt=detail.pending_interrupt,
        )


__all__ = [
    "DefaultInterruptTag",
    "DefaultPipelineEvent",
    "DefaultPipelineRunDetail",
    "ExecutionInterrupt",
    "ExecutionInterruptValues",
    "ExecutionReviewPayload",
    "PlanApprovalPayload",
    "PlanApproved",
    "PlanInterrupt",
    "PlanInterruptValues",
    "PlanReady",
    "ReflectionComplete",
    "TriageComplete",
    "WaveComplete",
]
