"""Artifact catalogue — storage, metadata index, and service."""

from ._memory import InMemoryCatalogueClient
from ._metadata import ArtifactMetadata
from ._protocol import CatalogueClient
from ._service import CatalogueService
from ._storage import FilesystemStorage, StorageBackend

__all__ = [
    "ArtifactMetadata",
    "CatalogueClient",
    "CatalogueService",
    "FilesystemStorage",
    "InMemoryCatalogueClient",
    "StorageBackend",
]
