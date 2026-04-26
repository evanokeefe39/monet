# monet.artifacts.prebuilt — Reference Implementation

## Responsibility

Concrete artifact storage using fsspec (blobs) + SQLite/SQLAlchemy (metadata index).
All files private except `ArtifactService` and `artifacts_from_env` exported from
`__init__.py`.

## Layout

| File | Owns |
|------|------|
| `_service.py` | `ArtifactService(storage_url, index_url)` — composes storage + index |
| `_storage.py` | `FsspecStorage` — multi-provider blob storage via fsspec |
| `_index.py` | `SQLiteIndex` + `ArtifactRecord` ORM + `Base` metadata |
| `_metadata.py` | `ArtifactMetadata` TypedDict (internal schema) |
| `_migrations.py` | Alembic helpers (`apply_migrations`, `check_at_head`, etc.) |

## FsspecStorage

All blocking IO via `asyncio.to_thread`. No `.resolve()`, `os.getcwd()`, or
`os.path.realpath()` in write/read paths (enforced by regression test).
`storage_url` is any fsspec URL: `file:///path`, `s3://bucket/prefix`, etc.

## ArtifactService concrete methods

`query(**filters)` and `count_per_thread(thread_ids)` are NOT on the protocol.
Callers isinstance-check before calling. Server routes use `get_artifact_backend()`
to access the concrete type.

## alembic target

Migrations reference this package's `Base` (via `monet._migrations.env`). New
schema changes require a migration version under `src/monet/_migrations/versions/`.
