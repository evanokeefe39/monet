# Agent Registration Collapse — Implementation Plan

Executes ADR-003. One refactor, breaking wire change, pre-1.0.

Phases ordered by safety: internal types first, then wire protocol, then deletions. Each phase commits independently; no long-lived branch.

## Phase 1 — Introduce `CapabilityIndex` alongside existing manifest

- [ ] Create `src/monet/server/_capabilities.py` containing:
  - `class Capability(BaseModel)` — pydantic model: `agent_id: str`, `command: str`, `pool: str`, `description: str = ""`, validators reject empty, charset `[a-z0-9_-]+`, len ≤ 64 (description ≤ 512).
  - `class CapabilityIndex` — `{(agent_id, command) → {pool, worker_ids: set[str], description}}`, `{worker_id → set[(agent_id, command)]}`. Methods: `upsert_worker(worker_id, pool, caps: list[Capability])`, `drop_worker(worker_id) -> list[Capability]`, `get_pool(agent_id, command) -> str | None`, `capabilities() -> list[dict]`, `slash_commands() -> list[str]` (port from `AgentManifest.slash_commands`), `worker_for_pool(worker_id, pool) -> bool` (pool-claim auth).
- [ ] Unit tests `tests/test_capability_index.py`:
  - `test_upsert_two_workers_one_capability_both_tracked` — both worker_ids appear in `worker_ids` set.
  - `test_drop_worker_keeps_capability_when_other_worker_serves_it` — last-writer-wins removed.
  - `test_drop_worker_removes_orphan_capability` — capability with empty `worker_ids` pruned.
  - `test_invalid_pool_charset_rejected` — pydantic 422-shaped error.
  - `test_slash_commands_merge_with_reserved` — `/plan` precedes `/agent_id:command`.
- [ ] Commit: `feat(server): CapabilityIndex + pydantic Capability model`.

## Phase 2 — Rename `AgentRegistry` → `LocalRegistry`, strip manifest coupling

- [ ] Rename class in `src/monet/core/registry.py`: `AgentRegistry` → `LocalRegistry`. Keep `default_registry` symbol (now a `LocalRegistry`) for backward-compat during the refactor.
- [ ] Delete `default_manifest.declare(...)` call at `src/monet/core/decorator.py:357`. Decorator now writes only `LocalRegistry`.
- [ ] Update all imports: `from monet.core.registry import AgentRegistry` → `LocalRegistry`. Run `uv run ruff check` + `uv run mypy src/`.
- [ ] Tests: `tests/test_decorator.py` — assert `@agent` writes `default_registry` only; import of `monet.core.manifest` is not required.
- [ ] Commit: `refactor(core): LocalRegistry rename; drop manifest side-effect from @agent`.

## Phase 3 — Add heartbeat endpoint + wire `WorkerClient.heartbeat` to it

- [ ] Add route `POST /api/v1/workers/{worker_id}/heartbeat` in `src/monet/server/_routes.py`. Body: `{pool: str, capabilities: list[Capability]}`. Handler calls `capability_index.upsert_worker(...)` and `worker_store.touch(worker_id, pool, now)`. Returns `200` with `{worker_id, known_capabilities: int}`.
- [ ] Add `require_api_key` dependency.
- [ ] Add `GET /api/v1/agents` auth: `Depends(require_api_key)` (fix DO-37).
- [ ] Flip `WorkerClient.register()` + `WorkerClient.heartbeat()` to POST the new endpoint. `register()` becomes a synonym for the first heartbeat — call-site in `cli/_worker.py:260` unchanged.
- [ ] Tests `tests/test_worker_heartbeat_endpoint.py`:
  - first heartbeat from unknown `worker_id` registers.
  - second heartbeat reconciles (drops stale caps, adds new).
  - invalid capability (empty `agent_id`) returns 422.
  - `GET /agents` without bearer returns 401.
- [ ] Commit: `feat(server): unified heartbeat endpoint; @require_api_key on /agents`.

## Phase 4 — Pool-scoped claim auth

- [ ] `POST /api/v1/pools/{pool}/claim` validates authenticated `worker_id` (from bearer or body) appears in `capability_index.worker_for_pool(worker_id, pool)`. Reject with 403 otherwise.
- [ ] Update `WorkerClient.claim()` to include its `worker_id` in the request (body or header).
- [ ] Tests: `test_claim_rejects_worker_not_in_pool`, `test_claim_accepts_heartbeating_worker`.
- [ ] Commit: `feat(server): pool-scoped claim auth`.

## Phase 5 — Collapse bootstrap to one path; ASGI lifespan

- [ ] Delete `async def bootstrap(...)` in `src/monet/server/_bootstrap.py`. Keep `AgentCapability` re-export only if needed (it won't be — pydantic model lives in `_capabilities.py`).
- [ ] Delete `configure_lazy_worker()`. Replace with an `@asynccontextmanager async def lifespan(app)` in `src/monet/server/__init__.py` that starts the in-process worker task when `ServerConfig.in_process_worker` is true (new explicit bool flag, default True for S1 monolith, false for split deployments — replaces implicit `ArtifactsConfig.distributed` branching).
- [ ] `src/monet/server/server_bootstrap.py` module body keeps: config validate, tracing, artifacts, queue, `configure_queue`, logger setup. Remove the `import monet.agents` eager-import (no longer needed — manifest populates via worker heartbeat).
- [ ] Remove `ArtifactsConfig.distributed` flag + branches in `_bootstrap.py:105-110` and `server_bootstrap.py:90-91`. Artifacts always configure on the server.
- [ ] Tests: `tests/test_lifespan_starts_worker.py` — spawn FastAPI TestClient, assert worker task is running; assert it's not started when `in_process_worker = False`.
- [ ] Commit: `refactor(server): one bootstrap; ASGI lifespan replaces lazy_worker monkey-patch`.

## Phase 6 — S1 loopback: in-process worker uses ASGITransport

- [ ] `monet dev` (`src/monet/cli/_dev.py`): when Aegra boots, construct a `MonetClient` + `WorkerClient` pointed at an `httpx.ASGITransport(app=aegra_app)` instance — no TCP. Pass this client into `run_worker(...)` spawned under the ASGI lifespan.
- [ ] `monet dev` awaits first successful heartbeat before printing `ready`.
- [ ] Tests: `tests/e2e/test_s1_loopback_heartbeat.py` — `monet dev`-equivalent fixture asserts `/agents` returns the expected capability set within 2s of boot.
- [ ] Commit: `feat(cli): S1 loopback worker via ASGITransport`.

## Phase 7 — Deletions

- [ ] Delete `src/monet/core/manifest.py`, `src/monet/core/agent_manifest.py`, `src/monet/agent_manifest.py`.
- [ ] Delete `src/monet/cli/_register.py`. Remove `register` subcommand from `src/monet/cli/__init__.py`.
- [ ] Delete `POST /api/v1/deployments`, `POST /api/v1/worker/register` in `_routes.py`. Delete `Deployments` store (`src/monet/server/_deployments.py` if it exists — check first).
- [ ] Delete `src/monet/server/_bootstrap.py::configure_lazy_worker` and the whole module if now empty.
- [ ] Grep-clean: no remaining imports of deleted symbols. `uv run mypy src/` green.
- [ ] Commit: `refactor: delete dual manifest + dual registration wire`.

## Phase 8 — Docs + examples

- [ ] Update `docs/architecture/deployment-scenarios.md` — new registration diagrams for S1/S2/S3.
- [ ] Update `docs/guides/distribution.md` — `monet worker` is the only registration CLI.
- [ ] Update `examples/deployed/worker/` and `examples/split-fleet/` — remove `monet register` references.
- [ ] Update `CLAUDE.md` — new file layout under `core/` and `server/`.
- [ ] Commit: `docs: registration collapse`.

## Done criteria

- `uv run pytest` green (including new tests for Phases 1–6).
- `uv run ruff check` + `uv run mypy src/` green.
- Grep: no occurrences of `AgentManifest`, `default_manifest`, `configure_agent_manifest`, `get_agent_manifest`, `configure_lazy_worker`, `monet register` in `src/`.
- S1: `monet dev` shows agents in `monet chat` within 2s of boot.
- S2: `monet worker --server-url` heartbeats to `/workers/{id}/heartbeat`; `GET /agents` reflects capabilities within one heartbeat interval.

## Out of scope

- Tenant scoping on registration (Priority 1 roadmap).
- Credential rotation (DO-25).
- Push-pool registration (push workers don't heartbeat today; unchanged).
- `reconcile_worker` audit log (RR-34 trigger deferred).

## Deferred (remaining after landing)

These items from the ADR are non-blocking bridges that stay in place
until follow-on PRs migrate orchestration fully off the legacy manifest.

- **`src/monet/core/manifest.py` / `core/agent_manifest.py` /
  `agent_manifest.py` deletions.** The three files are still alive as a
  bridge: `orchestration/_invoke.py` reads `default_manifest.get_pool()`
  after checking the local registry, `queue/_worker.py` configures the
  manifest handle for in-process access, `agents/planner/__init__.py`
  reads `get_agent_manifest().list_agents()`. The `_aegra_routes.py`
  lifespan mirrors heartbeat updates into `default_manifest` to keep
  those reads working. Trigger: orchestration + planner cut over to
  `CapabilityIndex`-typed reads (likely via a request-scoped
  `CapabilityIndex` passed through the graph hook registry).

- **`AgentCapability` TypedDict retirement.** Still used by
  `_routes.py`, `worker_client.py`, `cli/_worker.py`, and
  `_deployment.py` as an internal shape. Replace with `Capability` (the
  pydantic model) once the manifest bridge is removed — the wire format
  is already the pydantic model.

- **S1 loopback via explicit `ASGITransport`.** Phase 6 landed as an
  in-process worker that claims directly from the shared queue and
  upserts the `CapabilityIndex` synchronously (no HTTP roundtrip, same
  data path as S2). The plan originally called for `httpx.ASGITransport`
  loopback; the direct-queue approach is simpler and has the same
  properties. Revisit only if auth / observability uniformity demands
  that every worker path go through HTTP.

- **Legacy `GET /api/v1/tasks/claim/{pool}` endpoint.** Still in
  `_routes.py` marked as RemoteQueue backwards compat. No current caller
  uses it (pull workers moved to `POST /pools/{pool}/claim`). Trigger:
  remove alongside `RemoteQueue`'s fallback path when push-pool parity
  is confirmed.
