"""Tests for typed exceptions."""

from __future__ import annotations

from monet.exceptions import EscalationRequired, NeedsHumanReview, SemanticError


def test_needs_human_review() -> None:
    exc = NeedsHumanReview(reason="Low confidence")
    assert exc.reason == "Low confidence"
    assert str(exc) == "Low confidence"
    assert isinstance(exc, Exception)


def test_escalation_required() -> None:
    exc = EscalationRequired(reason="Needs admin access")
    assert exc.reason == "Needs admin access"
    assert isinstance(exc, Exception)


def test_semantic_error() -> None:
    exc = SemanticError(type="no_results", message="Nothing found")
    assert exc.type == "no_results"
    assert exc.message == "Nothing found"
    assert isinstance(exc, Exception)


def test_semantic_error_defaults() -> None:
    exc = SemanticError()
    assert exc.type == "unknown"
    assert exc.message == ""
