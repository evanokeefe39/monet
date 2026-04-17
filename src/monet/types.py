"""Public types for the monet agent SDK.

All types used across the SDK, artifact store, and orchestration layers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict

from .signals import SignalType

__all__ = [
    "AgentMeta",
    "AgentResult",
    "AgentRunContext",
    "ApprovalAction",
    "ArtifactPointer",
    "ChatMessage",
    "EnvelopeField",
    "EnvelopeFieldOption",
    "InterruptEnvelope",
    "Signal",
    "SignalType",
    "build_artifact_pointer",
    "find_artifact",
]

_log = logging.getLogger("monet.types")

# --- Approval action (single canonical source) ---

ApprovalAction = Literal["approve", "revise", "reject"]
"""Canonical vocabulary for plan-approval HITL decisions.

Both the orchestration layer (``_forms.py``) and the TUI
(``_chat_app.py``) import from here so the literal strings live in
exactly one place.
"""


# --- Chat message ---


class ChatMessage(TypedDict):
    """A single turn in a chat transcript (role + content)."""

    role: Literal["user", "assistant", "system"]
    content: str


# --- Interrupt form envelope (validated wire contract) ---

_KNOWN_FIELD_TYPES = frozenset(
    {
        "text",
        "textarea",
        "int",
        "float",
        "bool",
        "select",
        "radio",
        "checkbox",
        "date",
        "artifact_ref",
        "markdown",
        "hidden",
    }
)


class EnvelopeFieldOption(BaseModel):
    """One selectable option inside a ``radio``/``checkbox``/``select`` field."""

    model_config = ConfigDict(extra="allow")
    value: str = ""
    label: str = ""


class EnvelopeField(BaseModel):
    """A single input in an :class:`InterruptEnvelope`.

    ``type`` is an open string for forward-compatibility — unknown types
    are allowed and should be rendered as ``text`` by consumers that don't
    recognise them.
    """

    model_config = ConfigDict(extra="allow")
    name: str = ""
    type: str = "text"
    label: str = ""
    options: list[EnvelopeFieldOption] = []
    default: Any = None
    required: bool = True
    help: str = ""
    value: Any = None

    def is_known_type(self) -> bool:
        """Return True when ``type`` is in the closed field vocabulary."""
        return self.type in _KNOWN_FIELD_TYPES


class InterruptEnvelope(BaseModel):
    """Validated wire contract for HITL interrupt payloads.

    Graphs pass a dict to ``interrupt()``. Consumers call
    ``InterruptEnvelope.model_validate(values)`` to get a typed view with
    helper methods. Unknown extra fields are preserved (``extra="allow"``)
    for forward-compatibility. Unknown ``field.type`` values are allowed
    and should fall back to plain-text rendering.

    ``protocol_version`` defaults to 1; future breaking vocab changes
    bump the version.
    """

    model_config = ConfigDict(extra="allow")
    protocol_version: int = 1
    prompt: str = ""
    fields: list[EnvelopeField] = []
    context: dict[str, Any] = {}
    render: Literal["inline", "modal"] = "modal"

    def is_approval_form(self) -> bool:
        """True when the form has an ``action`` radio with approve/reject options."""
        for f in self.fields:
            if f.name == "action" and f.type == "radio":
                values = {o.value for o in f.options}
                if {"approve", "reject"}.issubset(values):
                    return True
        return False

    @classmethod
    def from_interrupt_values(cls, values: Any) -> InterruptEnvelope | None:
        """Parse *values* from an interrupt payload. Returns None on failure."""
        if not isinstance(values, dict):
            return None
        try:
            return cls.model_validate(values)
        except Exception:
            _log.debug("InterruptEnvelope parse failed", exc_info=True)
            return None


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
    """Reference to a stored artifact.

    key is an optional semantic tag. Set at write time, consumed
    by find_artifact() at lookup time.
    """

    key: str


def find_artifact(
    artifacts: tuple[ArtifactPointer, ...], key: str
) -> ArtifactPointer | None:
    """Return the first artifact matching a semantic key, or None."""
    return next((a for a in artifacts if a.get("key") == key), None)


def build_artifact_pointer(raw: dict[str, Any]) -> ArtifactPointer:
    """Reconstruct an ArtifactPointer from a raw dict, preserving optional fields.

    Single codec for every path that rehydrates a pointer from wire bytes
    (queue serialisation, HTTP request bodies, test fixtures). Preserves
    the optional ``key`` semantic tag that ``find_artifact`` depends on.
    """
    pointer = ArtifactPointer(
        artifact_id=raw.get("artifact_id", ""),
        url=raw.get("url", ""),
    )
    key = raw.get("key")
    if isinstance(key, str):
        pointer["key"] = key
    return pointer


# --- Agent run context ---


class AgentRunContext(TypedDict, total=False):
    """Runtime context available inside a decorated agent function.

    Set via ContextVar by the decorator. Accessible via get_run_context()
    or by declaring matching parameter names on the agent function.

    ``thread_id`` is optional — populated for agent calls that originate
    under a LangGraph / Aegra thread (chat or the default pipeline).
    Tools that need thread-scoped telemetry (e.g. ``artifact_query`` in
    the chat TUI) read it from here.
    """

    task: str
    context: list[dict[str, Any]]
    command: str
    trace_id: str
    run_id: str
    agent_id: str
    skills: list[str]
    thread_id: str


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
