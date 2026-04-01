"""Tests for the context system."""

from __future__ import annotations

from monet._context import get_run_context, get_run_logger


def test_get_run_context_outside_decorator() -> None:
    ctx = get_run_context()
    assert ctx.task == ""
    assert ctx.command == "fast"
    assert ctx.agent_id == ""


def test_get_run_logger_outside_decorator() -> None:
    logger = get_run_logger()
    # Should return a logger (no-op logger for the "noop" namespace)
    assert logger is not None
    # Should not raise when called
    logger.info("test message outside decorator")
