"""Storage backends for artifact binary content + metadata sidecars."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class StorageBackend(Protocol):
    """Abstract storage for artifact content and metadata."""

    def write(
        self,
        artifact_id: str,
        content: bytes,
        metadata_dict: dict[str, Any],
    ) -> str:
        """Write content and metadata. Returns a URL for the content."""
        ...

    def read(self, artifact_id: str) -> tuple[bytes, dict[str, Any]]:
        """Read content and metadata dict."""
        ...


class FilesystemStorage:
    """Filesystem backend: {root}/{id}/content + {root}/{id}/meta.json."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(
        self,
        artifact_id: str,
        content: bytes,
        metadata_dict: dict[str, Any],
    ) -> str:
        """Write content and metadata to filesystem.

        Preconditions:
            artifact_id is non-empty.
        Postconditions:
            Binary written to {root}/{artifact_id}/content.
            JSON written to {root}/{artifact_id}/meta.json.
        """
        artifact_dir = self._root / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        (artifact_dir / "content").write_bytes(content)
        (artifact_dir / "meta.json").write_text(json.dumps(metadata_dict, indent=2))

        return f"file://{artifact_dir / 'content'}"

    def read(self, artifact_id: str) -> tuple[bytes, dict[str, Any]]:
        """Read content and metadata from filesystem.

        Preconditions:
            artifact_id directory exists.
        Postconditions:
            Returns content bytes and parsed metadata dict.
        """
        artifact_dir = self._root / artifact_id
        if not artifact_dir.exists():
            msg = f"Artifact not found: {artifact_id}"
            raise KeyError(msg)

        content = (artifact_dir / "content").read_bytes()
        meta_dict: dict[str, Any] = json.loads((artifact_dir / "meta.json").read_text())
        return content, meta_dict
