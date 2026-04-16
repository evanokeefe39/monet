"""Tests for _signal_router — declarative signal routing."""

from __future__ import annotations

from monet.orchestration._signal_router import EXECUTION_ROUTER, SignalRouter
from monet.signals import BLOCKING, SignalType


def _signal(signal_type: str) -> dict:  # type: ignore[type-arg]
    return {"type": signal_type, "reason": "test", "metadata": None}


def test_blocking_signal_routes_to_interrupt() -> None:
    route = EXECUTION_ROUTER.route([_signal(SignalType.NEEDS_HUMAN_REVIEW)])
    assert route is not None
    assert route.action == "interrupt"


def test_needs_clarification_is_blocking() -> None:
    """NEEDS_CLARIFICATION must be in the BLOCKING group — agents asking
    follow-up questions block until the human answers."""
    assert SignalType.NEEDS_CLARIFICATION in BLOCKING
    route = EXECUTION_ROUTER.route([_signal(SignalType.NEEDS_CLARIFICATION)])
    assert route is not None
    assert route.action == "interrupt"


def test_recoverable_signal_routes_to_retry() -> None:
    route = EXECUTION_ROUTER.route([_signal(SignalType.RATE_LIMITED)])
    assert route is not None
    assert route.action == "retry"


def test_blocking_wins_over_recoverable() -> None:
    """Both present — BLOCKING rule fires first (registration order)."""
    signals = [
        _signal(SignalType.RATE_LIMITED),
        _signal(SignalType.NEEDS_HUMAN_REVIEW),
    ]
    route = EXECUTION_ROUTER.route(signals)
    assert route is not None
    assert route.action == "interrupt"


def test_no_matching_signals_returns_none() -> None:
    """Informational signals don't match any rule."""
    route = EXECUTION_ROUTER.route([_signal(SignalType.LOW_CONFIDENCE)])
    assert route is None


def test_unknown_signal_type_no_match() -> None:
    route = EXECUTION_ROUTER.route([_signal("totally_unknown_type")])
    assert route is None


def test_semantic_error_routes_as_recoverable() -> None:
    """SEMANTIC_ERROR is now in RECOVERABLE group."""
    route = EXECUTION_ROUTER.route([_signal(SignalType.SEMANTIC_ERROR)])
    assert route is not None
    assert route.action == "retry"


def test_empty_signals_returns_none() -> None:
    route = EXECUTION_ROUTER.route([])
    assert route is None


def test_custom_router() -> None:
    """Custom router with different rules works correctly."""
    router = SignalRouter().on_group(BLOCKING, "escalate")
    route = router.route([_signal(SignalType.NEEDS_HUMAN_REVIEW)])
    assert route is not None
    assert route.action == "escalate"
    # RECOVERABLE not registered, so no match.
    assert router.route([_signal(SignalType.RATE_LIMITED)]) is None
