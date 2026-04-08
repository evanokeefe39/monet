# Artifact Catalogue

The catalogue is a thin storage layer for agent outputs. It stores binary content alongside structured metadata and makes artifacts addressable by URL.

## Overview

Agents produce artifacts -- research findings, reports, analyses, plans. The catalogue stores these with full provenance (who created it, when, as part of which trace and run) and makes them retrievable by any agent or external system via standard HTTP.

The architecture separates storage (where bytes live) from indexing (how you query metadata). Both are pluggable.

| Environment | Storage backend | Metadata index |
|---|---|---|
| Local development | Filesystem | SQLite |
| Production | S3 / GCS / any fsspec backend | Postgres |

The switch is pure configuration. No code changes.

## `CatalogueClient` protocol

The minimal interface for reading and writing artifacts:

```python
from monet.catalogue import CatalogueClient, ArtifactMetadata

class CatalogueClient(Protocol):
    def write(self, content: bytes, metadata: ArtifactMetadata) -> ArtifactPointer: ...
    def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]: ...
```

Any class implementing these two methods satisfies the protocol (it is `runtime_checkable`).

## `ArtifactMetadata`

A Pydantic model carrying all structured metadata for an artifact:

| Field | Type | Required | Description |
|---|---|---|---|
| `artifact_id` | `str` | auto-generated | Unique identifier (UUID) |
| `content_type` | `str` | yes | MIME type |
| `content_length` | `int` | auto | Byte size, computed at write time |
| `content_encoding` | `str` | no | Present if compressed |
| `content_hash` | `str` | auto | SHA-256 checksum, computed at write time |
| `summary` | `str` | recommended | Bounded text summary for routing decisions |
| `schema_version` | `str` | yes (default `"1"`) | Metadata schema version |
| `created_by` | `str` | yes | Agent name |
| `created_at` | `datetime` | auto | Timestamp |
| `trace_id` | `str` | no | OTel trace ID |
| `run_id` | `str` | no | LangGraph run ID |
| `invocation_command` | `str` | no | Command that produced this artifact |
| `invocation_effort` | `str` | no | Effort level at invocation time |
| `confidence` | `float` | no (default `0.0`) | 0.0--1.0 |
| `completeness` | `str` | no (default `"complete"`) | `complete`, `partial`, or `resource-bounded` |
| `sensitivity_label` | `str` | no (default `"public"`) | `public`, `internal`, `confidential`, `restricted` |
| `data_residency` | `str` | no | Storage jurisdiction |
| `retention_policy` | `str` | no | Duration or expiry (required if PII) |
| `pii_flag` | `bool` | no (default `False`) | Whether artifact contains PII |
| `tags` | `dict` | no | Free-form key-value pairs |

Validation: if `pii_flag` is `True`, `retention_policy` must be set. This is enforced by Pydantic validation at construction time.

## Storage backends

### `FilesystemStorage`

The default local backend. Stores artifacts at `{root}/{artifact_id}/content` (binary) and `{root}/{artifact_id}/meta.json`. Returns `file://` URLs.

```python
from monet.catalogue import FilesystemStorage

storage = FilesystemStorage(root="/tmp/monet-catalogue")
```

### `StorageBackend` protocol

Implement this to add custom backends (S3, GCS, etc.):

```python
from monet.catalogue import StorageBackend

class StorageBackend(Protocol):
    def write(self, artifact_id: str, content: bytes, metadata_dict: dict) -> str: ...
    def read(self, artifact_id: str) -> tuple[bytes, dict]: ...
```

`write()` returns a URL string. `read()` returns content bytes and the metadata dict.

## `CatalogueService`

Composes a storage backend with the SQLite metadata index:

```python
from monet.catalogue import CatalogueService, FilesystemStorage

storage = FilesystemStorage(root="/tmp/monet-catalogue")
service = CatalogueService(storage=storage, db_url="sqlite:///catalogue.db")
```

The service:

- Generates a UUID if `artifact_id` is not set
- Computes SHA-256 content hash
- Timestamps the metadata
- Writes to the storage backend
- Indexes metadata in SQLite
- On read, verifies integrity via hash comparison

## `InMemoryCatalogueClient`

A dict-backed implementation for tests:

```python
from monet.catalogue import InMemoryCatalogueClient

client = InMemoryCatalogueClient()
pointer = client.write(b"hello", metadata)
content, meta = client.read(pointer.artifact_id)
```

Auto-generates UUIDs and computes hashes. No external dependencies.

## Using the catalogue from agents

The SDK function `write_artifact()` wraps the catalogue client:

```python
from monet import write_artifact
from monet.catalogue import InMemoryCatalogueClient, configure_catalogue

# At startup
configure_catalogue(InMemoryCatalogueClient())

# Inside an agent function
pointer = await write_artifact(
    content=report.encode(),
    content_type="text/markdown",
    summary="Market analysis report",
    confidence=0.85,
    completeness="complete",
    sensitivity_label="internal",
)
```

`write_artifact()` is async — it forwards to `await get_catalogue().write(...)`. The pointer it returns is also appended to `AgentResult.artifacts` automatically. Stamping (`trace_id`, `run_id`, `agent_id`) is handled by the `CatalogueService`. Raises `NotImplementedError` if no catalogue backend is configured.
