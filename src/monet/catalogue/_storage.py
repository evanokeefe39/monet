"""Filesystem storage backend for artifact binary content + metadata sidecars.

Writes bytes to a local filesystem directory. Each artifact gets a subdirectory
under root/ keyed by artifact_id containing content (bytes) and meta.json.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles

from monet.types import ArtifactPointer

if TYPE_CHECKING:
    from monet.catalogue._metadata import ArtifactMetadata


class FilesystemStorage:
    """Filesystem backend: {root}/{id}/content + {root}/{id}/meta.json.

    All blocking filesystem operations (``Path.mkdir``, ``Path.exists``)
    run via :func:`asyncio.to_thread` so the async interface is honest
    all the way through. This matters under ASGI servers like LangGraph
    Server's dev runtime, which refuse to run sync fs calls on the event
    loop.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        # Constructor is sync by API contract; callers that need a fully
        # async startup should ``await asyncio.to_thread(FilesystemStorage, ...)``
        # or rely on lazy creation in ``write()``.
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
        await asyncio.to_thread(artifact_dir.mkdir, parents=True, exist_ok=True)

        async with aiofiles.open(artifact_dir / "content", "wb") as f:
            await f.write(content)
        async with aiofiles.open(artifact_dir / "meta.json", "w") as f:
            await f.write(json.dumps(metadata, indent=2))

        # ``self.root`` is guaranteed absolute by the caller (server_graphs.py
        # resolves it at import time), so the joined path is already absolute
        # and can be formatted by ``Path.as_uri()`` directly. Do NOT call
        # ``.resolve()`` here — on Windows it invokes ``os.path.realpath`` ->
        # ``os.getcwd`` which is a blocking syscall. Under ``langgraph dev``
        # that is intercepted by blockbuster and raised as a BlockingError,
        # killing every agent invocation with an empty AgentResult.
        return ArtifactPointer(
            artifact_id=artifact_id,
            url=(artifact_dir / "content").as_uri(),
        )

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Read content and metadata from filesystem.

        Preconditions:
            artifact_id directory exists.
        Postconditions:
            Returns content bytes and parsed metadata dict.
        """
        artifact_dir = self.root / artifact_id
        if not await asyncio.to_thread(artifact_dir.exists):
            msg = f"Artifact not found: {artifact_id}"
            raise KeyError(msg)

        async with aiofiles.open(artifact_dir / "content", "rb") as f:
            content = await f.read()
        async with aiofiles.open(artifact_dir / "meta.json") as f:
            raw = await f.read()
        try:
            metadata: ArtifactMetadata = json.loads(raw)
        except json.JSONDecodeError as exc:
            msg = f"Corrupt metadata for artifact {artifact_id}"
            raise ValueError(msg) from exc
        return content, metadata
