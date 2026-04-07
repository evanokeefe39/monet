"""Filesystem storage backend for artifact binary content + metadata sidecars.

Writes bytes to a local filesystem directory. Each artifact gets a subdirectory
under root/ keyed by artifact_id containing content (bytes) and meta.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiofiles

from monet.catalogue._metadata import ArtifactMetadata
from monet.types import ArtifactPointer


class FilesystemStorage:
    """Filesystem backend: {root}/{id}/content + {root}/{id}/meta.json."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    async def write(
        self, content: bytes, metadata: ArtifactMetadata
    ) -> ArtifactPointer:
        """Write content and metadata to filesystem.

        Preconditions:
            metadata["artifact_id"] is non-empty.
        Postconditions:
            Binary written to {root}/{artifact_id}/content.
            JSON written to {root}/{artifact_id}/meta.json.
        """
        artifact_id = metadata["artifact_id"]
        artifact_dir = self.root / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(artifact_dir / "content", "wb") as f:
            await f.write(content)
        async with aiofiles.open(artifact_dir / "meta.json", "w") as f:
            await f.write(json.dumps(metadata, indent=2))

        return ArtifactPointer(
            artifact_id=artifact_id,
            url=f"file://{artifact_dir / 'content'}",
        )

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Read content and metadata from filesystem.

        Preconditions:
            artifact_id directory exists.
        Postconditions:
            Returns content bytes and parsed metadata dict.
        """
        artifact_dir = self.root / artifact_id
        if not artifact_dir.exists():
            msg = f"Artifact not found: {artifact_id}"
            raise KeyError(msg)

        async with aiofiles.open(artifact_dir / "content", "rb") as f:
            content = await f.read()
        async with aiofiles.open(artifact_dir / "meta.json") as f:
            metadata: ArtifactMetadata = json.loads(await f.read())
        return content, metadata
