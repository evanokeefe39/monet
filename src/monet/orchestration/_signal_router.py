"""Declarative signal-group to routing-action mapping.

The current signal groups (BLOCKING, RECOVERABLE, etc.) are well-designed
but only consumed by hand-coded if/elif routing functions. SignalRouter
makes the group-to-action mapping declarative so new graphs define their
routing policy as data. Rules are evaluated in registration order; first
match wins.
"""

from __future__ import annotations

from typing import Any

from monet.signals import BLOCKING, RECOVERABLE, SignalType, in_group


class SignalRoute:
    """Result of routing: which action to take and why."""

    __slots__ = ("action", "signals")

    def __init__(self, action: str, signals: list[dict[str, Any]]) -> None:
        self.action = action
        self.signals = signals


class SignalRouter:
    """Declarative mapping from signal groups to routing actions.

    Rules are evaluated in registration order; first match wins.
    This matches the current semantics (BLOCKING checked before RECOVERABLE).
    """

    def __init__(self) -> None:
        self._rules: list[tuple[frozenset[SignalType], str]] = []

    def on_group(self, group: frozenset[SignalType], action: str) -> SignalRouter:
        """Register a rule: signals in ``group`` trigger ``action``."""
        self._rules.append((group, action))
        return self

    def route(self, signals: list[dict[str, Any]]) -> SignalRoute | None:
        """Evaluate signals against rules. Returns first matching route, or None."""
        for group, action in self._rules:
            matching = [s for s in signals if in_group(s.get("type", ""), group)]
            if matching:
                return SignalRoute(action=action, signals=matching)
        return None


# Default router for the execution graph (replaces inline logic in collect_wave).
EXECUTION_ROUTER = (
    SignalRouter().on_group(BLOCKING, "interrupt").on_group(RECOVERABLE, "retry")
)
