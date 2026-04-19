"""SignalType registered with langgraph msgpack allowlist (I1)."""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from langgraph.checkpoint.serde import (
    _msgpack as _lg_msgpack,  # type: ignore[import-untyped]
)
from langgraph.checkpoint.serde.jsonplus import (  # type: ignore[import-untyped]
    JsonPlusSerializer,
)

from monet.signals import SignalType


def test_signal_type_in_safe_msgpack_types() -> None:
    """Import of monet.signals registers SignalType with langgraph allowlist."""
    key = (SignalType.__module__, SignalType.__name__)
    assert key in _lg_msgpack.SAFE_MSGPACK_TYPES


def test_signal_type_roundtrips_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round-trip SignalType through the serializer; no deprecation warning."""
    serializer = JsonPlusSerializer()
    original = SignalType.ESCALATION_REQUIRED
    type_name, blob = serializer.dumps_typed(original)
    with caplog.at_level("WARNING"):
        decoded = serializer.loads_typed((type_name, blob))
    assert decoded == original
    for record in caplog.records:
        assert "unregistered type monet.signals.SignalType" not in record.getMessage()
        assert "Blocked deserialization of monet.signals.SignalType" not in (
            record.getMessage()
        )


def test_signal_type_preserved_in_list() -> None:
    """State typically carries list[Signal] with SignalType values."""
    serializer = JsonPlusSerializer()
    payload = [SignalType.NEEDS_HUMAN_REVIEW, SignalType.LOW_CONFIDENCE]
    type_name, blob = serializer.dumps_typed(payload)
    decoded = serializer.loads_typed((type_name, blob))
    assert decoded == payload
    assert all(isinstance(sig, SignalType) for sig in decoded)
