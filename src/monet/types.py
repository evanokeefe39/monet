"""Public types for the monet agent SDK.

All types used across the SDK, catalogue, and orchestration layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from .signals import SignalType

__all__ = [
    "AgentMeta",
    "AgentResult",
    "AgentRunContext",
    "ArtifactPointer",
    "Signal",
    "SignalType",
    "find_artifact",
]

# --- Signals (list-based accumulation model) ---


class Signal(TypedDict):
    """A single signal emitted by an agent.

    Signals accumulate — multiple can be true simultaneously.
    Non-fatal: the agent can continue execution and return a result
    alongside signals via emit_signal().
    Fatal conditions use typed exceptions instead.
    """

    type: str
    reason: str
    metadata: dict[str, Any] | None


# --- Agent metadata (passed to hooks) ---


class AgentMeta(TypedDict):
    """Metadata about the agent being invoked, passed to hook handlers."""

    agent_id: str
    command: str


# --- Artifact pointer ---


class _ArtifactPointerRequired(TypedDict):
    artifact_id: str
    url: str


class ArtifactPointer(_ArtifactPointerRequired, total=False):
    """Reference to an artifact in the catalogue.

    key is an optional semantic tag. Set at write time, consumed
    by find_artifact() at lookup time.
    """

    key: str


def find_artifact(
    artifacts: tuple[ArtifactPointer, ...], key: str
) -> ArtifactPointer | None:
    """Return the first artifact matching a semantic key, or None."""
    return next((a for a in artifacts if a.get("key") == key), None)


# --- Agent run context ---


class AgentRunContext(TypedDict):
    """Runtime context available inside a decorated agent function.

    Set via ContextVar by the decorator. Accessible via get_run_context()
    or by declaring matching parameter names on the agent function.
    """

    task: str
    context: list[dict[str, Any]]
    command: str
    trace_id: str
    run_id: str
    agent_id: str
    skills: list[str]


# --- Agent result ---


@dataclass(frozen=True)
class AgentResult:
    """Wrapped result from an agent invocation.

    Never constructed manually by the function author. The decorator
    builds this from the function's return value or raised exception.
    """

    success: bool
    output: str | dict[str, Any] | None = None
    artifacts: tuple[ArtifactPointer, ...] = ()
    signals: tuple[Signal, ...] = ()
    trace_id: str = ""
    run_id: str = ""

    def has_signal(self, signal_type: SignalType) -> bool:
        """Check if signals contain a signal of the given type."""
        return any(s["type"] == signal_type for s in self.signals)

    def get_signal(self, signal_type: SignalType) -> Signal | None:
        """Get the first signal of the given type, or None."""
        return next((s for s in self.signals if s["type"] == signal_type), None)
