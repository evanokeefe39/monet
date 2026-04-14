"""Tests for core data types."""

from __future__ import annotations

from monet.types import (
    AgentResult,
    AgentRunContext,
    ArtifactPointer,
    Signal,
    SignalType,
    find_artifact,
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


# --- ArtifactPointer (TypedDict) ---


def test_artifact_pointer() -> None:
    p: ArtifactPointer = {
        "artifact_id": "abc-123",
        "url": "http://artifact store/abc-123",
    }
    assert p["artifact_id"] == "abc-123"
    assert p["url"] == "http://artifact store/abc-123"


def test_artifact_pointer_is_dict() -> None:
    p: ArtifactPointer = {"artifact_id": "x", "url": "y"}
    assert isinstance(p, dict)


def test_artifact_pointer_with_key() -> None:
    p: ArtifactPointer = {"artifact_id": "x", "url": "y", "key": "work_brief"}
    assert p["key"] == "work_brief"


def test_artifact_pointer_without_key() -> None:
    p: ArtifactPointer = {"artifact_id": "x", "url": "y"}
    assert "key" not in p


# --- find_artifact ---


def test_find_artifact_no_artifacts() -> None:
    assert find_artifact((), "work_brief") is None


def test_find_artifact_no_match() -> None:
    ptrs: tuple[ArtifactPointer, ...] = (
        ArtifactPointer(artifact_id="a1", url="u1", key="other"),
    )
    assert find_artifact(ptrs, "work_brief") is None


def test_find_artifact_single_match() -> None:
    ptrs: tuple[ArtifactPointer, ...] = (
        ArtifactPointer(artifact_id="a1", url="u1", key="work_brief"),
    )
    result = find_artifact(ptrs, "work_brief")
    assert result is not None
    assert result["artifact_id"] == "a1"


def test_find_artifact_first_match_wins() -> None:
    ptrs: tuple[ArtifactPointer, ...] = (
        ArtifactPointer(artifact_id="a1", url="u1", key="work_brief"),
        ArtifactPointer(artifact_id="a2", url="u2", key="work_brief"),
    )
    result = find_artifact(ptrs, "work_brief")
    assert result is not None
    assert result["artifact_id"] == "a1"


def test_find_artifact_without_key_field() -> None:
    """Artifacts without a key field are skipped."""
    ptrs: tuple[ArtifactPointer, ...] = (ArtifactPointer(artifact_id="a1", url="u1"),)
    assert find_artifact(ptrs, "work_brief") is None


# --- AgentRunContext (TypedDict) ---


def test_context_is_dict() -> None:
    ctx: AgentRunContext = {
        "task": "test",
        "context": [],
        "command": "fast",
        "trace_id": "",
        "run_id": "",
        "agent_id": "",
        "skills": [],
    }
    assert isinstance(ctx, dict)
    assert ctx["task"] == "test"
    assert ctx["command"] == "fast"
    assert ctx["skills"] == []


# --- AgentResult (frozen dataclass) ---


def test_result_success() -> None:
    r = AgentResult(success=True, output="done", trace_id="t1", run_id="r1")
    assert r.success is True
    assert r.output == "done"
    assert r.artifacts == ()
    assert r.signals == ()


def test_result_with_artifacts() -> None:
    ptr: ArtifactPointer = {"artifact_id": "a1", "url": "http://x"}
    r = AgentResult(success=True, output="done", artifacts=(ptr,))
    assert len(r.artifacts) == 1
    assert r.artifacts[0]["artifact_id"] == "a1"


def test_result_with_signals() -> None:
    signals: tuple[Signal, ...] = (
        {"type": SignalType.NEEDS_HUMAN_REVIEW, "reason": "Low", "metadata": None},
        {"type": SignalType.LOW_CONFIDENCE, "reason": "0.3", "metadata": None},
    )
    r = AgentResult(success=True, output="done", signals=signals)
    assert len(r.signals) == 2
    assert r.signals[0]["type"] == "needs_human_review"
    assert r.signals[1]["type"] == "low_confidence"


# --- AgentResult.has_signal / get_signal ---


def test_has_signal() -> None:
    signals: tuple[Signal, ...] = (
        {"type": SignalType.NEEDS_HUMAN_REVIEW, "reason": "Low", "metadata": None},
    )
    r = AgentResult(success=False, output="", signals=signals)
    assert r.has_signal(SignalType.NEEDS_HUMAN_REVIEW) is True
    assert r.has_signal(SignalType.LOW_CONFIDENCE) is False


def test_get_signal() -> None:
    signals: tuple[Signal, ...] = (
        {"type": SignalType.ESCALATION_REQUIRED, "reason": "Admin", "metadata": None},
    )
    r = AgentResult(success=False, output="", signals=signals)
    sig = r.get_signal(SignalType.ESCALATION_REQUIRED)
    assert sig is not None
    assert sig["reason"] == "Admin"
    assert r.get_signal(SignalType.LOW_CONFIDENCE) is None
