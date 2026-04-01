"""Tests for core data types."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from monet._types import (
    AgentResult,
    AgentRunContext,
    AgentSignals,
    ArtifactEntry,
    ArtifactPointer,
    ConstraintEntry,
    ContextEntry,
    InstructionEntry,
    SemanticErrorInfo,
    SkillReferenceEntry,
    WorkBriefEntry,
)

# --- AgentSignals ---


def test_signals_defaults() -> None:
    s = AgentSignals()
    assert s.needs_human_review is False
    assert s.review_reason is None
    assert s.escalation_requested is False
    assert s.semantic_error is None


def test_signals_frozen() -> None:
    s = AgentSignals()
    with pytest.raises(AttributeError):
        s.needs_human_review = True  # type: ignore[misc]


# --- SemanticErrorInfo ---


def test_semantic_error_info() -> None:
    e = SemanticErrorInfo(type="no_results", message="Nothing found")
    assert e.type == "no_results"
    assert e.message == "Nothing found"


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
    assert r.signals.needs_human_review is False


def test_result_with_confidence() -> None:
    r = AgentResult(success=True, output="done", confidence=0.85)
    assert r.confidence == 0.85


def test_result_with_artifacts() -> None:
    ptr = ArtifactPointer(artifact_id="a1", url="http://x")
    r = AgentResult(success=True, output="done", artifacts=[ptr])
    assert len(r.artifacts) == 1
    assert r.artifacts[0].artifact_id == "a1"


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
