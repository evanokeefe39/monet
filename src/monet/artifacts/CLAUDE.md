# monet.artifacts — Artifact Store

## Responsibility

Persists agent outputs as keyed blobs. Protocol surface is minimal (3 methods).
`prebuilt/` ships the reference implementation (`FsspecStorage` + `SQLiteIndex`).

## Public interface

Protocol (in `_protocol.py`):
```python
class ArtifactWriter: async def write(content, **kwargs) -> ArtifactPointer
class ArtifactReader: async def read(artifact_id) -> tuple[bytes, dict]; async def list(*, limit, cursor) -> list[ArtifactPointer]
class ArtifactClient(ArtifactReader, ArtifactWriter): ...
```

Test stub (in `_memory.py`):
```python
class InMemoryArtifactClient  # implements ArtifactClient
```

Reference implementation (in `prebuilt/`):
```python
class ArtifactService(storage_url: str, index_url: str):
    # Protocol methods: write, read, list
    # Concrete methods: query(**filters), count_per_thread(thread_ids)

def artifacts_from_env(*, default_root=None) -> ArtifactService
```

## write() kwargs

`ArtifactStore.write()` (SDK handle) resolves `AgentRunContext` and passes it as
`agent_run_ctxt` kwarg. All other kwargs pass through. `ArtifactService.write()`
recognises: `content_type`, `summary`, `confidence`, `completeness`,
`sensitivity_label`, `tags`, `key`, `agent_run_ctxt`.

## ArtifactPointer

Carries `artifact_id`, `url`, and optional `key`. Access is always by key —
never by position. `result.artifacts[0]` must not exist in calling code.

## Server routes

Routes that need metadata (list, count) check `isinstance(backend, ArtifactService)`.
Access the backend via `get_artifact_backend()` from `monet.core.artifacts`.

## Migrations

`prebuilt/_migrations.py` manages SQLiteIndex schema via Alembic. Run at
`ArtifactService.initialise()` for in-memory DBs, out-of-band via `monet db migrate`
for persistent DBs. `prebuilt/_index.py` owns the `Base` and `ArtifactRecord` ORM.

## What artifacts does NOT own

- Orchestration routing
- Content inspection or validation
- Cost metering
- Catalogue (execution-side, belongs to workers)

## Invariants

- Orchestration state holds pointers only — never artifact content
- `load_plan` is the single exception (planner resolves work_brief pointer)
- Artifacts are selected by key, never by position
