"""Core data types for the monet agent SDK.

All types used across the SDK, catalogue, and orchestration layers.
Named _types.py to avoid shadowing the stdlib types module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

# --- Effort enum ---

Effort = Literal["low", "medium", "high"]

VALID_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high"})


# --- Signals (list-based accumulation model) ---


class SignalType(StrEnum):
    """Standard signal types emitted by agents."""

    NEEDS_HUMAN_REVIEW = "needs_human_review"
    ESCALATION_REQUIRED = "escalation_required"
    LOW_CONFIDENCE = "low_confidence"
    PARTIAL_RESULT = "partial_result"
    REVISION_SUGGESTED = "revision_suggested"
    SENSITIVE_CONTENT = "sensitive_content"
    SEMANTIC_ERROR = "semantic_error"


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


# --- Artifact pointer ---


@dataclass(frozen=True)
class ArtifactPointer:
    """Reference to an artifact in the catalogue."""

    artifact_id: str
    url: str


# --- Context entries (pydantic discriminated union) ---


class ArtifactEntry(BaseModel):
    """Context entry pointing to a catalogue artifact."""

    type: Literal["artifact"] = "artifact"
    summary: str | None = None
    url: str | None = None
    content: str | None = None
    content_type: str | None = None


class WorkBriefEntry(BaseModel):
    """Context entry containing a work brief."""

    type: Literal["work_brief"] = "work_brief"
    summary: str | None = None
    url: str | None = None
    content: str | None = None
    content_type: str | None = None


class ConstraintEntry(BaseModel):
    """Context entry expressing a constraint."""

    type: Literal["constraint"] = "constraint"
    summary: str | None = None
    url: str | None = None
    content: str | None = None
    content_type: str | None = None


class InstructionEntry(BaseModel):
    """Context entry containing an instruction."""

    type: Literal["instruction"] = "instruction"
    summary: str | None = None
    url: str | None = None
    content: str | None = None
    content_type: str | None = None


class SkillReferenceEntry(BaseModel):
    """Context entry referencing a skill."""

    type: Literal["skill_reference"] = "skill_reference"
    summary: str | None = None
    url: str | None = None
    content: str | None = None
    content_type: str | None = None


ContextEntry = Annotated[
    ArtifactEntry
    | WorkBriefEntry
    | ConstraintEntry
    | InstructionEntry
    | SkillReferenceEntry,
    Field(discriminator="type"),
]

VALID_CONTEXT_TYPES: frozenset[str] = frozenset(
    {"artifact", "work_brief", "constraint", "instruction", "skill_reference"}
)


# --- Agent run context ---


@dataclass
class AgentRunContext:
    """Runtime context available inside a decorated agent function.

    Set via ContextVar by the decorator. Accessible via get_run_context()
    or by declaring matching parameter names on the agent function.
    """

    task: str = ""
    context: list[
        ArtifactEntry
        | WorkBriefEntry
        | ConstraintEntry
        | InstructionEntry
        | SkillReferenceEntry
    ] = field(default_factory=list)
    command: str = "fast"
    effort: Effort | None = None
    trace_id: str = ""
    run_id: str = ""
    agent_id: str = ""
    skills: list[str] = field(default_factory=list)


# --- Agent result ---


@dataclass(frozen=True)
class AgentResult:
    """Wrapped result from an agent invocation.

    Never constructed manually by the function author. The decorator
    builds this from the function's return value or raised exception.
    """

    success: bool
    output: str | ArtifactPointer
    confidence: float = 0.0
    artifacts: list[ArtifactPointer] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    trace_id: str = ""
    run_id: str = ""
