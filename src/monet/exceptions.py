"""Typed exceptions for agent signal propagation.

Raised by agent functions, caught by the decorator, translated into
AgentResult.signals. The function author never constructs signals manually.
"""

from __future__ import annotations


class NeedsHumanReview(Exception):  # noqa: N818
    """Agent requests human review. Partial artifacts are preserved."""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(reason)


class EscalationRequired(Exception):  # noqa: N818
    """Agent has hit a capability or permissions boundary."""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(reason)


class SemanticError(Exception):
    """Soft failure — no results, irreconcilable conflict, quality below threshold."""

    def __init__(self, type: str = "unknown", message: str = "") -> None:
        self.type = type
        self.message = message
        super().__init__(message)


class PushDispatchTerminal(SemanticError):  # noqa: N818
    """Push-pool webhook dispatch failed after exhausting retries or on terminal 4xx.

    The failure result is already written to the task queue before this is raised
    so ``wait_completion`` unblocks immediately with a DISPATCH_FAILED signal.
    """
