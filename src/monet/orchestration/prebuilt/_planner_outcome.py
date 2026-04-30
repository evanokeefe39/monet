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

from pydantic import BaseModel, ConfigDict, ValidationError

from monet.signals import SignalType
from monet.types import find_artifact

from ._state import RoutingSkeleton

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from monet.types import AgentResult, ArtifactPointer, Signal


class PlannerRawOutput(BaseModel):
    """Typed wrapper for the planner's structured output dict.

    extra='ignore' because LLM output frequently carries additional keys.
    """

    model_config = ConfigDict(extra="ignore")

    work_brief_artifact_id: str | None = None
    routing_skeleton: dict[str, Any] | None = None
    kind: str | None = None
    questions: list[Any] = []


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
        reason = signal.get("reason")
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

    try:
        raw = PlannerRawOutput.model_validate(output)
    except ValidationError:
        if any(s["type"] == SignalType.NEEDS_CLARIFICATION for s in signals):
            return QuestionsOutcome(questions=[])
        return PlannerFailure(
            reason=f"Planner returned non-dict output: {type(output).__name__}"
        )

    questions = _extract_questions(raw, signals)
    if questions is not None:
        return QuestionsOutcome(questions=questions)

    pointer = find_artifact(result.artifacts, "work_brief")
    if pointer is None:
        return PlannerFailure(
            reason=(
                f"Planner did not produce a work_brief artifact "
                f"({len(result.artifacts)} artifact(s) returned)."
            )
        )

    if (
        raw.work_brief_artifact_id
        and raw.work_brief_artifact_id != pointer["artifact_id"]
    ):
        return PlannerFailure(
            reason=(
                f"Planner reported artifact_id '{raw.work_brief_artifact_id}' "
                f"but keyed artifact has '{pointer['artifact_id']}'."
            )
        )

    if not raw.routing_skeleton:
        return PlannerFailure(
            reason="Planner did not return routing_skeleton in output."
        )
    try:
        RoutingSkeleton.model_validate(raw.routing_skeleton)
    except ValidationError as exc:
        return PlannerFailure(reason=f"Routing skeleton invalid: {exc}")

    return PlanOutcome(
        work_brief_pointer=pointer,
        routing_skeleton=raw.routing_skeleton,
        raw_output=output if isinstance(output, dict) else {},
    )


def _extract_questions(
    raw: PlannerRawOutput,
    signals: Sequence[Signal],
) -> list[str] | None:
    """Return questions if planner is asking for clarification, else None."""
    signalled = any(s["type"] == SignalType.NEEDS_CLARIFICATION for s in signals)
    kind_questions = raw.kind == "questions"
    if not (signalled or kind_questions):
        return None
    return [str(q).strip() for q in raw.questions if str(q).strip()]
