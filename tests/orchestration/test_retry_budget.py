"""Tests for _retry_budget — shared retry counter."""

from __future__ import annotations

from monet.orchestration._retry_budget import (
    check_budget,
    increment_budget,
    reset_budget,
)


def test_check_budget_under_limit() -> None:
    assert check_budget({"revision_count": 2}, max_retries=3) is True


def test_check_budget_at_limit() -> None:
    assert check_budget({"revision_count": 3}, max_retries=3) is False


def test_check_budget_over_limit() -> None:
    assert check_budget({"revision_count": 5}, max_retries=3) is False


def test_check_budget_missing_key() -> None:
    """Missing revision_count treated as 0."""
    assert check_budget({}, max_retries=3) is True


def test_increment_budget() -> None:
    update = increment_budget({"revision_count": 2})
    assert update == {"revision_count": 3}


def test_increment_budget_from_zero() -> None:
    update = increment_budget({})
    assert update == {"revision_count": 1}


def test_reset_budget() -> None:
    assert reset_budget() == {"revision_count": 0}
