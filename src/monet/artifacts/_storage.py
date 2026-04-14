"""Filesystem storage backend for artifact binary content + metadata sidecars.

Writes bytes to a local filesystem directory. Each artifact gets a subdirectory
under root/ keyed by artifact_id containing content (bytes) and meta.json.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles  # type: ignore[import-untyped]

from monet.types import ArtifactPointer

if TYPE_CHECKING:
    from monet.artifacts._metadata import ArtifactMetadata


class FilesystemStorage:
    """Filesystem backend: {root}/{id}/content + {root}/{id}/meta.json.

    All blocking filesystem operations (``Path.mkdir``, ``Path.exists``)
    run via :func:`asyncio.to_thread` so the async interface is honest
    all the way through. This matters under ASGI servers like LangGraph
    Server's dev runtime, which refuse to run sync fs calls on the event
    loop.
    """

    def __init__(self, root: str | Path) -> None:
        # Enforce the "root is absolute" invariant at construction time so
        # the async ``write()`` path can format ``file://`` URIs via
        # ``Path.as_uri()`` without ever calling ``.absolute()`` /
        # ``.resolve()`` itself. Both of those internally hit
        # ``os.getcwd``, which ``blockbuster`` intercepts under
        # ``langgraph dev`` and raises as a ``BlockingError`` — the same
        # incident guarded by
        # ``test_filesystem_storage_no_blocking_syscalls_in_write``.
        #
        # Calling ``.absolute()`` is safe *here* because the constructor
        # is sync and runs at import time or during explicit startup,
        # outside any ASGI event loop. Callers that want a fully async
        # startup can still ``await asyncio.to_thread(FilesystemStorage, ...)``.
        self.root = Path(root).absolute()
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

        # ``self.root`` is absolute — enforced in ``__init__`` — so the
        # joined path is already absolute and can be formatted by
        # ``Path.as_uri()`` directly. Do NOT call ``.resolve()`` /
        # ``.absolute()`` here — both invoke ``os.getcwd`` on Windows
        # (and ``os.path.realpath`` in the resolve case), which
        # ``blockbuster`` intercepts under ``langgraph dev`` and raises
        # as a ``BlockingError``, killing every agent invocation with an
        # empty AgentResult. See
        # ``test_filesystem_storage_no_blocking_syscalls_in_write``.
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
