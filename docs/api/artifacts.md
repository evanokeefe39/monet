# Artifact Store API Reference

All exports from `monet.artifacts`.

## `ArtifactClient`

```python
@runtime_checkable
class ArtifactClient(Protocol):
    def write(self, content: bytes, metadata: ArtifactMetadata) -> ArtifactPointer: ...
    def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]: ...
    def query_recent(
        self,
        *,
        agent_id: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[ArtifactMetadata]: ...
```

Protocol for artifact store implementations. Any class with `write`, `read`, and `query_recent` methods satisfying these signatures is a valid client.

`query_recent` returns artifact metadata ordered by `created_at` descending. All filters are optional; `tag` matches a tag *key* in the stored tag dict; `since` is an ISO-8601 timestamp. Implementations that cannot efficiently query may raise `NotImplementedError` — callers handle that case when swapping in backends like read-only S3.

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

## `ArtifactService`

```python
class ArtifactService:
    def __init__(self, storage: StorageBackend, db_url: str = "sqlite:///artifact store.db") -> None
```

Composes a `StorageBackend` with a `SQLiteIndex`. Implements `ArtifactClient`.

- `write()` -- generates UUID, computes SHA-256, timestamps, writes to storage, indexes metadata
- `read()` -- retrieves from storage, verifies content hash integrity

## `InMemoryArtifactClient`

```python
class InMemoryArtifactClient:
    def __init__(self) -> None
```

Dict-backed implementation for testing. Implements `ArtifactClient`. Auto-generates UUIDs and computes content hashes.
