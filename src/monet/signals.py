"""Signal vocabulary and routing groups.

Signals are non-fatal events accumulated during agent execution. Fatal
conditions raise typed exceptions instead. The orchestrator routes on
signal *groups*, never on raw string matching, so adding a new signal
type to a group automatically updates routing without code changes at
the call site.
"""

from __future__ import annotations

from enum import StrEnum


class SignalType(StrEnum):
    """Standard signal types emitted by agents."""

    # Control flow — orchestrator routes on these directly
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    ESCALATION_REQUIRED = "escalation_required"
    APPROVAL_REQUIRED = "approval_required"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    DEPENDENCY_FAILED = "dependency_failed"
    RATE_LIMITED = "rate_limited"
    TOOL_UNAVAILABLE = "tool_unavailable"

    # Informational — feeds QA reflection verdict, not direct routing
    LOW_CONFIDENCE = "low_confidence"
    PARTIAL_RESULT = "partial_result"
    CONFLICTING_SOURCES = "conflicting_sources"
    REVISION_SUGGESTED = "revision_suggested"

    # Audit — recorded in state, no routing consequence
    EXTERNAL_ACTION_TAKEN = "external_action_taken"
    CONTENT_OFFLOADED = "content_offloaded"
    SENSITIVE_CONTENT = "sensitive_content"

    # Failure
    SEMANTIC_ERROR = "semantic_error"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"


BLOCKING: frozenset[SignalType] = frozenset(
    {
        SignalType.NEEDS_HUMAN_REVIEW,
        SignalType.ESCALATION_REQUIRED,
        SignalType.APPROVAL_REQUIRED,
    }
)
RECOVERABLE: frozenset[SignalType] = frozenset(
    {
        SignalType.INSUFFICIENT_CONTEXT,
        SignalType.DEPENDENCY_FAILED,
        SignalType.RATE_LIMITED,
        SignalType.TOOL_UNAVAILABLE,
        SignalType.SEMANTIC_ERROR,
        SignalType.CAPABILITY_UNAVAILABLE,
    }
)
INFORMATIONAL: frozenset[SignalType] = frozenset(
    {
        SignalType.LOW_CONFIDENCE,
        SignalType.PARTIAL_RESULT,
        SignalType.CONFLICTING_SOURCES,
        SignalType.REVISION_SUGGESTED,
    }
)
AUDIT: frozenset[SignalType] = frozenset(
    {
        SignalType.EXTERNAL_ACTION_TAKEN,
        SignalType.CONTENT_OFFLOADED,
        SignalType.SENSITIVE_CONTENT,
    }
)
ROUTING: frozenset[SignalType] = BLOCKING | RECOVERABLE


def in_group(signal_type_str: str, group: frozenset[SignalType]) -> bool:
    """Membership test that accepts the raw string from a Signal TypedDict."""
    try:
        return SignalType(signal_type_str) in group
    except ValueError:
        return False


def _register_in_msgpack_allowlist() -> None:
    """Add SignalType to langgraph's msgpack safe-types allowlist.

    Without this, every checkpoint resume that carries a SignalType logs
    a "Deserializing unregistered type" warning and will hard-fail under
    ``LANGGRAPH_STRICT_MSGPACK=true`` or a future langgraph release.
    Extends ``_lg_msgpack.SAFE_MSGPACK_TYPES`` because ``_check_allowed``
    in ``jsonplus.py`` resolves the module attribute at runtime.
    """
    try:
        from langgraph.checkpoint.serde import _msgpack as _lg_msgpack
    except ImportError:
        return
    key = (SignalType.__module__, SignalType.__name__)
    if key in _lg_msgpack.SAFE_MSGPACK_TYPES:
        return
    _lg_msgpack.SAFE_MSGPACK_TYPES = frozenset(
        set(_lg_msgpack.SAFE_MSGPACK_TYPES) | {key}
    )


_register_in_msgpack_allowlist()


__all__ = [
    "AUDIT",
    "BLOCKING",
    "INFORMATIONAL",
    "RECOVERABLE",
    "ROUTING",
    "SignalType",
    "in_group",
]
