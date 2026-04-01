"""Core data types for the monet agent SDK.

All types used across the SDK, catalogue, and orchestration layers.
Named _types.py to avoid shadowing the stdlib types module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

# --- Effort enum ---

Effort = Literal["low", "medium", "high"]

VALID_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high"})


# --- Signals ---


@dataclass(frozen=True)
class SemanticErrorInfo:
    """Structured info for a semantic error signal."""

    type: str
    message: str


@dataclass(frozen=True)
class AgentSignals:
    """Signals emitted by an agent, read by the orchestrator."""

    needs_human_review: bool = False
    review_reason: str | None = None
    escalation_requested: bool = False
    escalation_reason: str | None = None
    revision_notes: dict[str, Any] | None = None
    semantic_error: SemanticErrorInfo | None = None


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
    context: list[Any] = field(default_factory=list)  # list[ContextEntry]
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
    artifacts: list[ArtifactPointer] = field(default_factory=list)
    signals: AgentSignals = field(default_factory=AgentSignals)
    trace_id: str = ""
    run_id: str = ""
