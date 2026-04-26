"""fsspec-based blob storage backend. Internal to prebuilt."""

from __future__ import annotations

import asyncio
from typing import cast

from monet.types import ArtifactPointer


class FsspecStorage:
    """Multi-provider blob storage via fsspec.

    root_url: any fsspec-supported URL prefix (file://, s3://, gs://, etc.).
    Each artifact is stored at root_url/artifact_id.

    All blocking IO runs via asyncio.to_thread so the async interface is
    honest throughout. This matters under ASGI servers that intercept sync
    fs calls on the event loop.
    """

    def __init__(self, root_url: str) -> None:
        # Strip trailing slash so _blob_url always produces clean paths.
        self._root = root_url.rstrip("/")

    def url_for(self, artifact_id: str) -> str:
        """Return the storage URL for a given artifact_id."""
        return f"{self._root}/{artifact_id}"

    async def write(self, content: bytes, artifact_id: str) -> ArtifactPointer:
        """Write bytes to storage and return a pointer."""
        url = self.url_for(artifact_id)
        await asyncio.to_thread(self._sync_write, url, content)
        return ArtifactPointer(artifact_id=artifact_id, url=url)

    def _sync_write(self, url: str, content: bytes) -> None:
        import fsspec  # type: ignore[import-not-found]

        fs, path = fsspec.url_to_fs(url)
        parent = "/".join(path.replace("\\", "/").rstrip("/").split("/")[:-1])
        if parent:
            fs.makedirs(parent, exist_ok=True)
        with fs.open(path, "wb") as f:
            f.write(content)

    async def read(self, artifact_id: str) -> bytes:
        """Read bytes from storage. Raises KeyError when artifact is absent."""
        url = self.url_for(artifact_id)
        return await asyncio.to_thread(self._sync_read, url, artifact_id)

    def _sync_read(self, url: str, artifact_id: str) -> bytes:
        import fsspec  # type: ignore[import-not-found]

        fs, path = fsspec.url_to_fs(url)
        if not fs.exists(path):
            msg = f"Artifact not found: {artifact_id}"
            raise KeyError(msg)
        with fs.open(path, "rb") as f:
            return cast("bytes", f.read())
