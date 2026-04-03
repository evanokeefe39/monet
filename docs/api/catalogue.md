# Catalogue API Reference

All exports from `monet.catalogue`.

## `CatalogueClient`

```python
@runtime_checkable
class CatalogueClient(Protocol):
    def write(self, content: bytes, metadata: ArtifactMetadata) -> ArtifactPointer: ...
    def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]: ...
```

Protocol for catalogue implementations. Any class with `write` and `read` methods satisfying these signatures is a valid client.

## `ArtifactMetadata`

```python
class ArtifactMetadata(BaseModel):
    artifact_id: str = ""
    content_type: str                    # required
    content_length: int = 0
    content_encoding: str = ""
    content_hash: str = ""
    summary: str = ""
    schema_version: str = "1"
    created_by: str                      # required
    created_at: datetime | None = None
    trace_id: str = ""
    run_id: str = ""
    invocation_command: str = ""
    invocation_effort: str | None = None
    confidence: float = 0.0
    completeness: Literal["complete", "partial", "resource-bounded"] = "complete"
    sensitivity_label: Literal["public", "internal", "confidential", "restricted"] = "public"
    data_residency: str = ""
    retention_policy: str = ""
    pii_flag: bool = False
    tags: dict[str, str] = {}
```

Pydantic model for artifact metadata. Validation: if `pii_flag` is `True`, `retention_policy` must be non-empty.

## `StorageBackend`

```python
class StorageBackend(Protocol):
    def write(self, artifact_id: str, content: bytes, metadata_dict: dict) -> str: ...
    def read(self, artifact_id: str) -> tuple[bytes, dict]: ...
```

Protocol for pluggable storage. `write()` returns a URL string. `read()` returns content bytes and metadata dict.

## `FilesystemStorage`

```python
class FilesystemStorage:
    def __init__(self, root: str | Path) -> None
```

Local filesystem storage backend. Stores at `{root}/{artifact_id}/content` and `{root}/{artifact_id}/meta.json`. Returns `file://` URLs.

## `CatalogueService`

```python
class CatalogueService:
    def __init__(self, storage: StorageBackend, db_url: str = "sqlite:///catalogue.db") -> None
```

Composes a `StorageBackend` with a `SQLiteIndex`. Implements `CatalogueClient`.

- `write()` -- generates UUID, computes SHA-256, timestamps, writes to storage, indexes metadata
- `read()` -- retrieves from storage, verifies content hash integrity

## `InMemoryCatalogueClient`

```python
class InMemoryCatalogueClient:
    def __init__(self) -> None
```

Dict-backed implementation for testing. Implements `CatalogueClient`. Auto-generates UUIDs and computes content hashes.
