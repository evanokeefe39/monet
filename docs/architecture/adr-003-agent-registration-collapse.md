# ADR-003: Agent Registration Collapse

**Status:** Proposed
**Date:** 2026-04-18
**Frame:** Eliminate dual-path complexity in agent registration so every deployment scenario (S1–S5) runs the same code. Pre-1.0; breaking wire changes acceptable.

---

## Context

Two overlapping concepts carry the same information:

- **`AgentRegistry`** (`src/monet/core/registry.py`) — `{(agent_id, command) → handler}`. Worker-side. Populated by `@agent`.
- **`AgentManifest`** (`src/monet/core/manifest.py`) — `{(agent_id, command) → {pool, worker_id, description}}`. Server-side. Populated by worker heartbeats AND by `@agent` side-effect when the same process hosts both.

The split produces:

1. **Two bootstrap paths.** `server/_bootstrap.py::bootstrap()` (async, `agents=`, `lazy_worker=`) and `server/server_bootstrap.py` (import-time side effects). They wire the same subsystems with different ordering guarantees.
2. **Two registration wire paths.** `POST /api/v1/deployments` (via `monet register`) and `POST /api/v1/worker/register` (via `monet worker --server-url`). Same payload shape `(pool, capabilities)`, different downstream effects.
3. **Two CLIs.** `monet register` and `monet worker`. No guidance on which to use.
4. **Handle indirection.** `core/agent_manifest.py` + top-level `agent_manifest.py` form a ContextVar-style handle over `default_manifest`.
5. **Cross-layer side-effect.** `@agent` in the SDK layer writes `default_manifest.declare(...)` in the orchestration layer.
6. **Global mutable singletons.** `default_registry` and `default_manifest` populated at import time. Two replicas → divergent manifests.

Architecture review (`architecture-checks`, 2026-04-18) flagged the cluster at DR-10, DR-20, DR-23, ST-03, ST-05, ST-07, ST-12, ST-19, DO-18, DO-22, DO-37, DO-39, RR-18, RR-19, RR-34.

---

## Decision

1. **One concept per side, two types.**
   - **`LocalRegistry`** (worker): `{(agent_id, command) → handler}`. Built by `@agent`. Every worker has one. That's it.
   - **`CapabilityIndex`** (server): `{(agent_id, command) → {pool, worker_ids, description}}`. Populated **only** by worker heartbeats. No handler. No `@agent` coupling.

2. **Delete the manifest layer.** Remove `src/monet/core/manifest.py`, `src/monet/core/agent_manifest.py`, `src/monet/agent_manifest.py`. Move `CapabilityIndex` into `src/monet/server/_capabilities.py`. Move `LocalRegistry` into `src/monet/core/registry.py` (rename from `AgentRegistry`).

3. **Heartbeat IS registration.** Single endpoint: `POST /api/v1/workers/{worker_id}/heartbeat` with body `{pool, capabilities}`. First heartbeat from an unknown `worker_id` registers; subsequent heartbeats reconcile. Delete `POST /worker/register` and `POST /deployments`. Delete `monet register` CLI. Delete the `Deployments` store (absorbed into worker liveness tracking).

4. **`@agent` is worker-only.** Decorator writes `LocalRegistry` only. Drop the `default_manifest.declare(...)` call at `core/decorator.py:357`. The server never imports `monet.agents`.

5. **Validated capability wire format.** Replace `AgentCapability` TypedDict with a pydantic model. Required fields: `agent_id`, `command`, `pool` (non-empty strings, charset `[a-z0-9_-]+`, max 64 chars each). Optional: `description` (max 512 chars). Rejection at the boundary, 422 response.

6. **One bootstrap.** Merge `server/_bootstrap.py::bootstrap()` into `server/server_bootstrap.py`. Kill the `lazy_worker` monkey-patch on `queue.enqueue`. Use an ASGI lifespan handler (`@asynccontextmanager` passed to `FastAPI(lifespan=...)`) for the S1 in-process worker spawn.

7. **S1 = S2 with an in-process loopback client.** `monet dev` starts the Aegra server and, in the same process, starts a `monet worker` using a loopback client that calls the server's FastAPI app via `httpx.ASGITransport` — not HTTP over TCP, but the same request/response path. One code path, no `distributed` fork. Remove the `ArtifactsConfig.distributed` flag and its branches in `_bootstrap.py` + `server_bootstrap.py` (artifacts configure unconditionally; worker-side artifact store is a transport concern, not a boot flag).

8. **Auth at `/agents` + pool-scoped claim.** `GET /api/v1/agents` gains `Depends(require_api_key)`. `POST /pools/{pool}/claim` validates the authenticated `worker_id` belongs to a worker currently heartbeating for that pool (close cross-pool poaching, fixes DO-36). Tenant scoping stays deferred to Priority 1.

---

## Net change

Deleted files (4):
- `src/monet/core/manifest.py`
- `src/monet/core/agent_manifest.py`
- `src/monet/agent_manifest.py`
- `src/monet/cli/_register.py`

Deleted/collapsed surface:
- `POST /api/v1/deployments`, `POST /api/v1/worker/register` → one `POST /api/v1/workers/{id}/heartbeat`.
- `async bootstrap()` → one module-level bootstrap in `server_bootstrap.py`.
- `monet register` CLI → one `monet worker` CLI.
- `configure_lazy_worker` → ASGI lifespan handler.
- `ArtifactsConfig.distributed` flag → removed.

New files (1):
- `src/monet/server/_capabilities.py` (contains `CapabilityIndex`).

---

## Tradeoffs

- **Breaking wire change.** External tooling calling `/deployments` or `/worker/register` breaks. Monet is pre-1.0; `examples/` and `docs/` updated in the same PR.
- **Reference agents need explicit import.** Today `monet.agents` imports happen via graph compilation. New model: `monet worker` eagerly imports `monet.agents` (one line, documented). User workers `import monet.agents` only if they want the reference roster.
- **S1 registration latency.** Chat UI sees 0 agents on the first refresh tick (sub-second) until the in-process worker's first heartbeat lands. Mitigation: `monet dev` awaits first heartbeat before reporting `ready`.
- **No pre-worker capability declaration.** `monet register` allowed declaring capabilities without a running worker. New model rejects phantom capabilities — a capability with zero live workers is unroutable, so declaring it was always a footgun.

---

## Migration / rollout

Single-PR refactor. No deprecation window (pre-1.0). Steps:

1. Add `CapabilityIndex` + `LocalRegistry` rename under feature-flagless new code paths.
2. Add `POST /workers/{id}/heartbeat`, gate behind `WorkerClient` version bump.
3. Flip `monet worker` to the new endpoint.
4. Delete old endpoints, old CLI, old manifest files in one commit.
5. Update `examples/deployed/*`, `docs/guides/distribution.md`, `docs/architecture/deployment-scenarios.md`.

---

## Deferred

- **Tenant scoping** on heartbeat / claim / `/agents`. Priority 1.
- **Credential rotation** without restart (DO-25). Trigger: operator pain.
- **`reconcile_worker` audit log** of dropped capabilities (RR-34). Trigger: first manifest-drift incident.
- **Capability registration idempotency key** beyond `worker_id`. Trigger: first observed duplicate-deployment bug in prod.

---

## References

- `architecture-checks` review 2026-04-18 (this session).
- CLAUDE.md `## Deployment scenarios` — S1–S6.
- `docs/architecture/deployment-scenarios.md` — S1–S5 wiring.
- User memories: no-ContextVar-indirection, no-env-mode-toggle, graph-agnostic-server.
