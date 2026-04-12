"""Typed events yielded by ``MonetClient.run()`` and query responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Run stream events ───────────────────────────────────────────────


@dataclass(frozen=True)
class TriageComplete:
    """Triage phase finished — topic classified."""

    run_id: str
    complexity: str
    suggested_agents: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlanReady:
    """Planner produced a work brief."""

    run_id: str
    goal: str
    phases: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlanApproved:
    """Plan was approved (auto or manual)."""

    run_id: str


@dataclass(frozen=True)
class PlanInterrupt:
    """Run paused — plan needs human approval.

    Use ``client.approve_plan``, ``client.revise_plan``, or
    ``client.reject_plan`` to continue.
    """

    run_id: str
    brief: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentProgress:
    """Streaming progress from an agent invocation."""

    run_id: str
    agent_id: str
    status: str
    reasons: str = ""


@dataclass(frozen=True)
class WaveComplete:
    """A wave of parallel agent invocations finished."""

    run_id: str
    phase_index: int
    wave_index: int
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
    """

    run_id: str
    reason: str
    phase_index: int
    wave_index: int


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
    """Full run state returned by ``get_run`` and ``get_results``."""

    run_id: str
    status: str
    phase: str
    triage: dict[str, Any] = field(default_factory=dict)
    work_brief: dict[str, Any] = field(default_factory=dict)
    wave_results: list[dict[str, Any]] = field(default_factory=list)
    wave_reflections: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PendingDecision:
    """A run waiting for human input, returned by ``list_pending``."""

    run_id: str
    decision_type: str  # "plan_approval" | "execution_review"
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
