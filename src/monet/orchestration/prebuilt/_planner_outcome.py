"""Planner-result classification shared by chat and planning subgraphs.

Both graphs invoke ``planner:plan`` and do the same post-invocation work:
check success, extract the ``work_brief`` artifact pointer, validate the
inline routing skeleton, and detect the ``NEEDS_CLARIFICATION`` question
path. This module centralises that classification so the call sites
branch on a typed outcome rather than re-implementing extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from monet.signals import SignalType
from monet.types import find_artifact

from ._state import RoutingSkeleton

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from monet.types import AgentResult, ArtifactPointer, Signal


@dataclass(frozen=True)
class PlanOutcome:
    """Planner produced a usable plan with pointer + validated skeleton."""

    work_brief_pointer: ArtifactPointer
    routing_skeleton: dict[str, Any]  # RoutingSkeleton.model_dump()
    raw_output: dict[str, Any]


@dataclass(frozen=True)
class QuestionsOutcome:
    """Planner needs clarification before it can plan."""

    questions: list[str]


@dataclass(frozen=True)
class PlannerFailure:
    """Planner did not produce a plan or valid questions."""

    reason: str


PlannerResult = PlanOutcome | QuestionsOutcome | PlannerFailure


def format_signal_reasons(
    signals: Iterable[Signal | dict[str, Any]],
    *,
    max_chars: int = 200,
) -> list[str]:
    """Return first-line, length-capped reasons from signals that carry one."""
    reasons: list[str] = []
    for signal in signals:
        reason = signal.get("reason") if isinstance(signal, dict) else None
        if not reason:
            continue
        first_line = str(reason).splitlines()[0][:max_chars]
        if first_line:
            reasons.append(first_line)
    return reasons


def classify_planner_result(result: AgentResult) -> PlannerResult:
    """Classify a planner AgentResult into plan, questions, or failure."""
    signals: Sequence[Signal] = result.signals
    output = result.output

    if not result.success:
        reasons = format_signal_reasons(signals)
        return PlannerFailure(
            reason="; ".join(reasons) if reasons else "Planner failed"
        )

    questions = _extract_questions(output, signals)
    if questions is not None:
        return QuestionsOutcome(questions=questions)

    if not isinstance(output, dict):
        return PlannerFailure(
            reason=f"Planner returned non-dict output: {type(output).__name__}"
        )

    pointer = find_artifact(result.artifacts, "work_brief")
    if pointer is None:
        return PlannerFailure(
            reason=(
                f"Planner did not produce a work_brief artifact "
                f"({len(result.artifacts)} artifact(s) returned)."
            )
        )

    reported_id = output.get("work_brief_artifact_id")
    if reported_id and reported_id != pointer["artifact_id"]:
        return PlannerFailure(
            reason=(
                f"Planner reported artifact_id '{reported_id}' "
                f"but keyed artifact has '{pointer['artifact_id']}'."
            )
        )

    skeleton_raw = output.get("routing_skeleton")
    if not skeleton_raw:
        return PlannerFailure(
            reason="Planner did not return routing_skeleton in output."
        )
    try:
        RoutingSkeleton.model_validate(skeleton_raw)
    except ValidationError as exc:
        return PlannerFailure(reason=f"Routing skeleton invalid: {exc}")

    return PlanOutcome(
        work_brief_pointer=pointer,
        routing_skeleton=skeleton_raw,
        raw_output=output,
    )


def _extract_questions(
    output: Any,
    signals: Sequence[Signal | dict[str, Any]],
) -> list[str] | None:
    """Return questions if planner is asking for clarification, else None."""
    signalled = any(
        isinstance(s, dict) and s.get("type") == SignalType.NEEDS_CLARIFICATION
        for s in signals
    )
    kind_questions = isinstance(output, dict) and output.get("kind") == "questions"
    if not (signalled or kind_questions):
        return None
    raw: list[Any] = []
    if isinstance(output, dict):
        raw = list(output.get("questions") or [])
    return [str(q).strip() for q in raw if str(q).strip()]
