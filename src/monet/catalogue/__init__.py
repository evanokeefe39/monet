"""Artifact catalogue — storage, metadata index, service, and configuration."""

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


__all__ = [
    "ArtifactMetadata",
    "CatalogueClient",
    "CatalogueService",
    "FilesystemStorage",
    "InMemoryCatalogueClient",
    "SQLiteIndex",
    "configure_catalogue",
]
