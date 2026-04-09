"""Retry budget — shared retry counter with scoped reset.

Replaces the duplicated revision_count + MAX_* implementations in
planning_graph and execution_graph with a single, testable interface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def check_budget(state: Mapping[str, Any], max_retries: int) -> bool:
    """True if revision_count < max_retries."""
    return (state.get("revision_count") or 0) < max_retries


def increment_budget(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return state update that increments revision_count."""
    return {"revision_count": (state.get("revision_count") or 0) + 1}


def reset_budget() -> dict[str, Any]:
    """Return state update that resets revision_count to 0."""
    return {"revision_count": 0}
