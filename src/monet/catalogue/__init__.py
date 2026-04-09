"""Artifact catalogue — storage, metadata index, service, and configuration."""

from __future__ import annotations

import os
from pathlib import Path

from monet._catalogue import _set_catalogue_backend

from ._index import SQLiteIndex
from ._memory import InMemoryCatalogueClient
from ._metadata import ArtifactMetadata
from ._protocol import CatalogueClient
from ._service import CatalogueService
from ._storage import FilesystemStorage


def configure_catalogue(client: CatalogueClient | None) -> None:
    """Configure the catalogue backend for this application.

    Call once at startup before any agents are invoked. Parallels
    configure_tracing(). Application supplies the implementation,
    SDK defines the interface.

    Pass None to reset — useful in test fixtures.

    Example:
        from monet.catalogue import (
            configure_catalogue, CatalogueService, FilesystemStorage, SQLiteIndex,
        )
        configure_catalogue(CatalogueService(
            storage=FilesystemStorage(".catalogue/artifacts"),
            index=SQLiteIndex("sqlite+aiosqlite:///.catalogue/index.db"),
        ))
    """
    _set_catalogue_backend(client)


def catalogue_from_env(*, default_root: Path | None = None) -> CatalogueService:
    """Create a CatalogueService from environment or default path.

    Resolves ``MONET_CATALOGUE_DIR`` env var if set, otherwise falls
    back to ``default_root``. Creates directories if needed.

    Args:
        default_root: Fallback path when ``MONET_CATALOGUE_DIR`` is unset.
            Defaults to ``Path(".catalogue")``.

    Returns:
        Configured CatalogueService ready for use.
    """
    env_override = os.environ.get("MONET_CATALOGUE_DIR", "").strip()
    root = Path(env_override) if env_override else (default_root or Path(".catalogue"))
    root.mkdir(parents=True, exist_ok=True)
    return CatalogueService(
        storage=FilesystemStorage(root=root / "artifacts"),
        index=SQLiteIndex(db_url=f"sqlite+aiosqlite:///{root / 'index.db'}"),
    )


__all__ = [
    "ArtifactMetadata",
    "CatalogueClient",
    "CatalogueService",
    "FilesystemStorage",
    "InMemoryCatalogueClient",
    "SQLiteIndex",
    "catalogue_from_env",
    "configure_catalogue",
]
