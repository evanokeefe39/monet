"""Artifact metadata model with write-time invariant validation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, model_validator


class ArtifactMetadata(BaseModel):
    """Metadata sidecar for a catalogue artifact.

    All spec-defined fields with pydantic validators enforcing
    write-time invariants.
    """

    artifact_id: str = ""
    content_type: str
    content_length: int = 0
    content_encoding: str | None = None
    content_hash: str = ""
    summary: str = ""
    schema_version: str = "1"
    created_by: str
    created_at: str = ""
    trace_id: str = ""
    run_id: str = ""
    invocation_command: str = ""
    invocation_effort: str | None = None
    confidence: float = 0.0
    completeness: Literal["complete", "partial", "resource-bounded"] = "complete"
    sensitivity_label: Literal["public", "internal", "confidential", "restricted"] = (
        "internal"
    )
    data_residency: str = "local"
    retention_policy: str | None = None
    pii_flag: bool = False
    tags: dict[str, Any] = {}

    @model_validator(mode="after")
    def _validate_pii_retention(self) -> ArtifactMetadata:
        """If pii_flag is True, retention_policy must be set."""
        if self.pii_flag and not self.retention_policy:
            msg = "retention_policy is required when pii_flag is True"
            raise ValueError(msg)
        return self
