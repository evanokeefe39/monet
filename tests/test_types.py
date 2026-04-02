"""Tests for core data types."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from monet._types import (
    AgentResult,
    AgentRunContext,
    ArtifactEntry,
    ArtifactPointer,
    ConstraintEntry,
    ContextEntry,
    InstructionEntry,
    Signal,
    SignalType,
    SkillReferenceEntry,
    WorkBriefEntry,
)

# --- Signal and SignalType ---


def test_signal_type_values() -> None:
    assert SignalType.NEEDS_HUMAN_REVIEW.value == "needs_human_review"
    assert SignalType.ESCALATION_REQUIRED.value == "escalation_required"
    assert SignalType.SEMANTIC_ERROR.value == "semantic_error"
    assert SignalType.LOW_CONFIDENCE.value == "low_confidence"


def test_signal_creation() -> None:
    s: Signal = {
        "type": SignalType.NEEDS_HUMAN_REVIEW,
        "reason": "Low confidence",
        "metadata": None,
    }
    assert s["type"] == "needs_human_review"
    assert s["reason"] == "Low confidence"


def test_signal_with_metadata() -> None:
    s: Signal = {
        "type": SignalType.SEMANTIC_ERROR,
        "reason": "No results",
        "metadata": {"error_type": "no_results"},
    }
    assert s["metadata"] == {"error_type": "no_results"}


# --- ArtifactPointer ---


def test_artifact_pointer() -> None:
    p = ArtifactPointer(artifact_id="abc-123", url="http://catalogue/abc-123")
    assert p.artifact_id == "abc-123"
    assert p.url == "http://catalogue/abc-123"


def test_artifact_pointer_frozen() -> None:
    p = ArtifactPointer(artifact_id="x", url="y")
    with pytest.raises(AttributeError):
        p.artifact_id = "z"  # type: ignore[misc]


# --- AgentRunContext ---


def test_context_defaults() -> None:
    ctx = AgentRunContext()
    assert ctx.task == ""
    assert ctx.context == []
    assert ctx.command == "fast"
    assert ctx.effort is None
    assert ctx.skills == []


def test_context_with_effort() -> None:
    ctx = AgentRunContext(effort="low")
    assert ctx.effort == "low"


# --- AgentResult ---


def test_result_success() -> None:
    r = AgentResult(success=True, output="done", trace_id="t1", run_id="r1")
    assert r.success is True
    assert r.output == "done"
    assert r.confidence == 0.0
    assert r.artifacts == []
    assert r.signals == []


def test_result_with_confidence() -> None:
    r = AgentResult(success=True, output="done", confidence=0.85)
    assert r.confidence == 0.85


def test_result_with_artifacts() -> None:
    ptr = ArtifactPointer(artifact_id="a1", url="http://x")
    r = AgentResult(success=True, output="done", artifacts=[ptr])
    assert len(r.artifacts) == 1
    assert r.artifacts[0].artifact_id == "a1"


def test_result_with_signals() -> None:
    signals: list[Signal] = [
        {"type": SignalType.NEEDS_HUMAN_REVIEW, "reason": "Low", "metadata": None},
        {"type": SignalType.LOW_CONFIDENCE, "reason": "0.3", "metadata": None},
    ]
    r = AgentResult(success=True, output="done", signals=signals)
    assert len(r.signals) == 2
    assert r.signals[0]["type"] == "needs_human_review"
    assert r.signals[1]["type"] == "low_confidence"


# --- ContextEntry discriminated union ---


_context_adapter: TypeAdapter[ContextEntry] = TypeAdapter(ContextEntry)


def test_context_entry_artifact() -> None:
    entry = _context_adapter.validate_python(
        {"type": "artifact", "summary": "A research paper", "url": "http://x"}
    )
    assert isinstance(entry, ArtifactEntry)
    assert entry.summary == "A research paper"


def test_context_entry_work_brief() -> None:
    entry = _context_adapter.validate_python(
        {"type": "work_brief", "content": "Write an essay"}
    )
    assert isinstance(entry, WorkBriefEntry)
    assert entry.content == "Write an essay"


def test_context_entry_constraint() -> None:
    entry = _context_adapter.validate_python(
        {"type": "constraint", "summary": "Max 500 words"}
    )
    assert isinstance(entry, ConstraintEntry)


def test_context_entry_instruction() -> None:
    entry = _context_adapter.validate_python(
        {"type": "instruction", "content": "Use formal tone"}
    )
    assert isinstance(entry, InstructionEntry)


def test_context_entry_skill_reference() -> None:
    entry = _context_adapter.validate_python(
        {"type": "skill_reference", "summary": "academic-writing-v2"}
    )
    assert isinstance(entry, SkillReferenceEntry)


def test_context_entry_invalid_type() -> None:
    with pytest.raises(ValidationError):
        _context_adapter.validate_python({"type": "invalid_type"})


def test_context_entry_missing_type() -> None:
    with pytest.raises(ValidationError):
        _context_adapter.validate_python({"summary": "no type"})
