# monet.artifacts — Artifact Store

## Responsibility

Persists agent outputs as keyed blobs. Composes `FilesystemStorage` (blobs on disk) and `SQLiteIndex` (metadata + queryability).

## Public interface

```python
class ArtifactService:
    async def initialise() -> None          # idempotent; called on first r/w
    async def write(content, mime_type, key, agent_id?, run_id?, trace_id?, tags?) -> ArtifactMetadata
    async def read(artifact_id) -> tuple[bytes, ArtifactMetadata]
    async def query_recent(limit, run_id?, agent_id?, tags?) -> list[ArtifactMetadata]
```

`ArtifactPointer` carries a `key` field. Access is always by key — never by position. `result.artifacts[0]` must not exist in calling code.

## write() auto-context

`write()` auto-pulls `agent_id`, `run_id`, `trace_id` from run context if available. Can be called outside agent decorator (context is optional).

## Migrations

`_migrations.py` manages SQLiteIndex schema via embedded Alembic-style versioned migrations. Run at `initialise()`.

## What artifacts does NOT own

- Orchestration routing
- Content inspection or validation
- Cost metering
- Catalogue (execution-side, belongs to workers)

## Invariants

- Orchestration state holds pointers only — never artifact content
- `load_plan` is the single exception (planner resolves work_brief pointer)
- Artifacts are selected by key, never by position
