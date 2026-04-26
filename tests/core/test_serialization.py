"""Tests for shared serialization helpers in core/serialization.py."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from monet.core.serialization import (
    deserialize_result,
    now_iso,
    safe_parse_context,
    serialize_result,
)
from monet.types import AgentResult, ArtifactPointer, Signal

# --- now_iso ---


def test_now_iso_returns_valid_iso_string() -> None:
    result = now_iso()
    # Should be parseable back to a datetime.
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo == UTC


def test_now_iso_is_utc() -> None:
    result = now_iso()
    assert "+00:00" in result or "Z" in result


# --- serialize_result / deserialize_result round-trip ---


def test_round_trip_minimal() -> None:
    original = AgentResult(success=True, output="hello")
    raw = serialize_result(original)
    restored = deserialize_result(raw)
    assert restored.success is True
    assert restored.output == "hello"
    assert restored.artifacts == ()
    assert restored.signals == ()


def test_round_trip_with_artifacts_and_signals() -> None:
    original = AgentResult(
        success=False,
        output={"key": "value"},
        artifacts=(ArtifactPointer(artifact_id="a1", url="https://example.com/a1"),),
        signals=(
            Signal(type="SEMANTIC_ERROR", reason="bad input", metadata={"k": "v"}),
        ),
        trace_id="t-123",
        run_id="r-456",
    )
    raw = serialize_result(original)
    restored = deserialize_result(raw)
    assert restored.success is False
    assert restored.output == {"key": "value"}
    assert len(restored.artifacts) == 1
    assert restored.artifacts[0]["artifact_id"] == "a1"
    assert len(restored.signals) == 1
    assert restored.signals[0]["type"] == "SEMANTIC_ERROR"
    assert restored.signals[0]["metadata"] == {"k": "v"}
    assert restored.trace_id == "t-123"
    assert restored.run_id == "r-456"


def test_round_trip_none_output() -> None:
    original = AgentResult(success=True, output=None)
    raw = serialize_result(original)
    restored = deserialize_result(raw)
    assert restored.output is None


def test_artifact_pointer_key_round_trip() -> None:
    """The optional semantic ``key`` field must survive queue serialization.

    Regression guard for ST-04: a prior deserializer dropped the field,
    breaking ``find_artifact(..., "work_brief")`` on any distributed
    deployment where results traverse the queue path.
    """
    pointer = ArtifactPointer(
        artifact_id="a-xyz", url="file:///tmp/brief", key="work_brief"
    )
    original = AgentResult(success=True, output=None, artifacts=(pointer,))
    restored = deserialize_result(serialize_result(original))
    assert len(restored.artifacts) == 1
    assert restored.artifacts[0]["artifact_id"] == "a-xyz"
    assert restored.artifacts[0].get("key") == "work_brief"


def test_artifact_pointer_without_key_round_trip() -> None:
    """Pointers written without ``key`` must still round-trip cleanly."""
    pointer = ArtifactPointer(artifact_id="a1", url="file:///tmp/a1")
    original = AgentResult(success=True, output=None, artifacts=(pointer,))
    restored = deserialize_result(serialize_result(original))
    assert len(restored.artifacts) == 1
    assert "key" not in restored.artifacts[0]


# --- deserialize_result error cases ---


def test_deserialize_result_invalid_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        deserialize_result("not-json")


def test_deserialize_result_missing_required_field() -> None:
    incomplete = json.dumps({"output": "x"})  # missing "success"
    with pytest.raises(KeyError):
        deserialize_result(incomplete)


# --- safe_parse_context ---


def test_safe_parse_context_valid() -> None:
    ctx = safe_parse_context('{"trace_id": "t1", "run_id": "r1"}')
    assert ctx is not None
    assert ctx["trace_id"] == "t1"


def test_safe_parse_context_none_input() -> None:
    assert safe_parse_context(None) is None


def test_safe_parse_context_invalid_json_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="monet.core.serialization"):
        result = safe_parse_context("{{broken", source="test")
    assert result is None
    assert "Corrupt context JSON" in caplog.text
    assert "test" in caplog.text


def test_safe_parse_context_non_string_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="monet.core.serialization"):
        # type: ignore[arg-type]
        result = safe_parse_context(12345, source="test.non_string")  # type: ignore[arg-type]
    assert result is None
    assert "Corrupt context JSON" in caplog.text
