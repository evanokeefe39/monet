"""Artifact metadata as a TypedDict."""

from __future__ import annotations

from typing import Any, TypedDict


class ArtifactMetadata(TypedDict):
    """Metadata sidecar for a catalogue artifact."""

    artifact_id: str
    content_type: str
    content_length: int
    summary: str
    confidence: float
    completeness: str  # "complete" | "partial" | "resource-bounded"
    sensitivity_label: str  # "public" | "internal" | "confidential"
    agent_id: str | None
    run_id: str | None
    trace_id: str | None
    tags: dict[str, Any]
    created_at: str  # ISO 8601
