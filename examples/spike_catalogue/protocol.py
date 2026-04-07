"""CatalogueClient protocol — the abstract interface for write_artifact().

Three implementations validate that the protocol is correct:
- InMemoryCatalogueClient: dict-backed, no I/O
- FilesystemCatalogueClient: temp dir, binary + meta.json sidecar
- (future) HttpCatalogueClient: calls catalogue FastAPI service
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

from monet.types import ArtifactPointer


@dataclass
class ArtifactMetadata:
    """Metadata sidecar for a catalogue artifact."""

    artifact_id: str
    content_type: str
    content_length: int
    content_hash: str
    summary: str = ""
    schema_version: str = "1"
    created_by: str = ""
    created_at: str = ""
    trace_id: str = ""
    run_id: str = ""
    invocation_command: str = ""
    invocation_effort: str | None = None
    confidence: float = 0.0
    completeness: str = "complete"
    sensitivity_label: str = "internal"
    data_residency: str = "local"
    pii_flag: bool = False
    retention_policy: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


def validate_metadata(meta: ArtifactMetadata) -> None:
    """Enforce write-time invariants per spec."""
    valid_labels = {"public", "internal", "confidential", "restricted"}
    if meta.sensitivity_label not in valid_labels:
        msg = (
            f"Invalid sensitivity_label: '{meta.sensitivity_label}'. "
            f"Must be one of {sorted(valid_labels)}"
        )
        raise ValueError(msg)

    valid_completeness = {"complete", "partial", "resource-bounded"}
    if meta.completeness not in valid_completeness:
        msg = (
            f"Invalid completeness: '{meta.completeness}'. "
            f"Must be one of {sorted(valid_completeness)}"
        )
        raise ValueError(msg)

    if meta.pii_flag and not meta.retention_policy:
        msg = "retention_policy is required when pii_flag is True"
        raise ValueError(msg)


@runtime_checkable
class CatalogueClient(Protocol):
    """Abstract interface for catalogue operations."""

    def write(self, content: bytes, metadata: ArtifactMetadata) -> ArtifactPointer: ...

    def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]: ...


class InMemoryCatalogueClient:
    """Dict-backed catalogue for tests. No I/O."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, ArtifactMetadata]] = {}

    def write(self, content: bytes, metadata: ArtifactMetadata) -> ArtifactPointer:
        validate_metadata(metadata)
        artifact_id = metadata.artifact_id or str(uuid.uuid4())
        metadata.artifact_id = artifact_id
        metadata.content_length = len(content)
        metadata.content_hash = hashlib.sha256(content).hexdigest()
        metadata.created_at = metadata.created_at or datetime.now(tz=UTC).isoformat()

        self._store[artifact_id] = (content, metadata)
        return ArtifactPointer(
            artifact_id=artifact_id,
            url=f"memory://{artifact_id}",
        )

    def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        if artifact_id not in self._store:
            msg = f"Artifact not found: {artifact_id}"
            raise KeyError(msg)
        return self._store[artifact_id]


class FilesystemCatalogueClient:
    """Filesystem-backed catalogue. Binary + meta.json sidecar."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(self, content: bytes, metadata: ArtifactMetadata) -> ArtifactPointer:
        validate_metadata(metadata)
        artifact_id = metadata.artifact_id or str(uuid.uuid4())
        metadata.artifact_id = artifact_id
        metadata.content_length = len(content)
        metadata.content_hash = hashlib.sha256(content).hexdigest()
        metadata.created_at = metadata.created_at or datetime.now(tz=UTC).isoformat()

        artifact_dir = self._root / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        # Write binary content
        (artifact_dir / "content").write_bytes(content)

        # Write metadata sidecar
        meta_dict = _metadata_to_dict(metadata)
        (artifact_dir / "meta.json").write_text(json.dumps(meta_dict, indent=2))

        return ArtifactPointer(
            artifact_id=artifact_id,
            url=f"file://{artifact_dir / 'content'}",
        )

    def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        artifact_dir = self._root / artifact_id
        if not artifact_dir.exists():
            msg = f"Artifact not found: {artifact_id}"
            raise KeyError(msg)

        content = (artifact_dir / "content").read_bytes()
        meta_dict = json.loads((artifact_dir / "meta.json").read_text())
        metadata = _dict_to_metadata(meta_dict)

        # Verify integrity
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != metadata.content_hash:
            msg = (
                f"Content hash mismatch for {artifact_id}: "
                f"expected {metadata.content_hash}, got {actual_hash}"
            )
            raise ValueError(msg)

        return content, metadata


def _metadata_to_dict(meta: ArtifactMetadata) -> dict[str, Any]:
    """Serialize metadata to a JSON-safe dict."""
    return {
        "artifact_id": meta.artifact_id,
        "content_type": meta.content_type,
        "content_length": meta.content_length,
        "content_hash": meta.content_hash,
        "summary": meta.summary,
        "schema_version": meta.schema_version,
        "created_by": meta.created_by,
        "created_at": meta.created_at,
        "trace_id": meta.trace_id,
        "run_id": meta.run_id,
        "invocation_command": meta.invocation_command,
        "invocation_effort": meta.invocation_effort,
        "confidence": meta.confidence,
        "completeness": meta.completeness,
        "sensitivity_label": meta.sensitivity_label,
        "data_residency": meta.data_residency,
        "pii_flag": meta.pii_flag,
        "retention_policy": meta.retention_policy,
        "tags": meta.tags,
    }


def _dict_to_metadata(d: dict[str, Any]) -> ArtifactMetadata:
    """Deserialize metadata from a JSON dict."""
    return ArtifactMetadata(
        artifact_id=d["artifact_id"],
        content_type=d["content_type"],
        content_length=d["content_length"],
        content_hash=d["content_hash"],
        summary=d.get("summary", ""),
        schema_version=d.get("schema_version", "1"),
        created_by=d.get("created_by", ""),
        created_at=d.get("created_at", ""),
        trace_id=d.get("trace_id", ""),
        run_id=d.get("run_id", ""),
        invocation_command=d.get("invocation_command", ""),
        invocation_effort=d.get("invocation_effort"),
        confidence=d.get("confidence", 0.0),
        completeness=d.get("completeness", "complete"),
        sensitivity_label=d.get("sensitivity_label", "internal"),
        data_residency=d.get("data_residency", "local"),
        pii_flag=d.get("pii_flag", False),
        retention_policy=d.get("retention_policy"),
        tags=d.get("tags", {}),
    )
