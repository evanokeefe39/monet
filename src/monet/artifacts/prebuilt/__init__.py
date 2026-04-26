"""Reference artifact implementation — ArtifactService + artifacts_from_env."""

from __future__ import annotations

from pathlib import Path

from monet.artifacts.prebuilt._service import ArtifactService


def artifacts_from_env(*, default_root: Path | None = None) -> ArtifactService:
    """Create an ArtifactService from env or fallback path.

    Resolves the root directory via ArtifactsConfig.load()
    (MONET_ARTIFACTS_DIR env), falling back to default_root and
    finally to Path(".artifacts"). Creates the directory if needed.

    Applies pending alembic migrations eagerly.
    """
    from monet.artifacts.prebuilt._migrations import apply_migrations
    from monet.config import ArtifactsConfig

    fallback = default_root or Path(".artifacts")
    root = ArtifactsConfig.load().resolve_root(fallback)
    root.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite+aiosqlite:///{root / 'index.db'}"
    apply_migrations(db_url)
    # Use absolute path as file:// URI so FsspecStorage can build valid URLs.
    storage_url = (root / "blobs").absolute().as_uri()
    return ArtifactService(storage_url=storage_url, index_url=db_url)


__all__ = ["ArtifactService", "artifacts_from_env"]
