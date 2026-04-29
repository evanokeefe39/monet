# Deployment & Run Scenarios

Six shapes the codebase supports, with the wiring each one relies on. S1–S3 are user-facing, S4 is a dev-loop detail, S5 is the SaaS shape, S6 is removed but often asked for.

## S1 — Local all-in-one (dev laptop)

One machine. `monet dev` starts Aegra plus Postgres and Redis in Docker. Worker pool is in-process (`pool="local"` runs as a background task in the server process). User invokes `monet run <topic>` or `monet chat`. `MonetClient` points at `http://localhost:2026`.

Target audience: tutorials, `examples/quickstart`, `examples/local`, `examples/custom-graph`.

Key files: `src/monet/cli/_dev.py`, `src/monet/server/_langgraph_config.py`, `src/monet/cli/_run.py`.

## S2 — Self-hosted production (single tenant)

User runs `aegra serve` plus managed Postgres plus managed Redis on their own infra. Workers run as separate processes or containers: `monet worker --server-url https://monet.internal --pool <p>`. Queue backend is Redis or Upstash.

Single `MONET_API_KEY` shared by workers and clients. `MonetClient(url=...)` from the user's app or CI.

Target audience: `examples/deployed/server/` + `examples/deployed/worker/` (two deployable services sharing `MONET_API_KEY` + Redis).

Key files: `src/monet/cli/_worker.py`, `src/monet/server/_auth.py`, `src/monet/queue/backends/redis.py`.

## S3 — Split fleet (same customer, multiple worker pools)

Same as S2 but N worker processes across regions or hardware classes, each claiming by `--pool` name declared in `monet.toml [pools]`. One logical server, several worker fleets.

Two pool execution modes exist:

- **Pull pools** — workers poll `claim()`, execute in-process, heartbeat lease every 30s via `renew_lease()`. No inbound ports required.
- **Cloud dispatch pools** — `dispatch = "ecs"` or `dispatch = "cloudrun"` in `monet.toml [pools.<name>]`. A thin dispatch worker polls `claim()`, submits the task to ECS/Cloud Run via outbound API call, then immediately claims next. The spawned container runs standard worker bootstrap, calls `complete()`/`fail()`, and heartbeats directly. No inbound ports on any worker.

Target audience: `examples/split-fleet/` (one server, two pools `fast` + `heavy`, shipped as both a Docker Compose stack and a Railway deployment).

Key files: `src/monet/config/_pools.py`, `src/monet/queue/_dispatch.py`, `src/monet/queue/backends/dispatch_ecs.py`, `src/monet/queue/backends/dispatch_cloudrun.py`, `src/monet/queue/_worker.py`.

## S4 — Workers-only local (test / library)

`monet worker` without `--server-url` uses `InMemoryTaskQueue` in-process. Exists today at `src/monet/cli/_worker.py`. Used by the test suite via the `_queue_worker` autouse fixture in `tests/conftest.py`.

Not a user-facing run mode — no pipeline composition happens here, no graph driver. Execution plane only. Useful for validating `@agent` + signals + artifact store in isolation.

## S5 — Vendor-hosted control plane, customer-hosted data plane (split-plane / SaaS)

The split-plane shape separates orchestration from telemetry by construction. The vendor runs `create_control_app` (claim/complete/fail, worker registration, graph invocation) on vendor infra. The customer runs `create_data_app` (event record/query/stream, artifacts) on their own infra. Customer telemetry and artifacts never traverse vendor infrastructure.

```
MonetClient(url="https://control.saas.com", data_plane_url="https://data.customer.com")
```

Control-plane methods (`run`, `resume`, `abort`, `list_runs`) target `url`. Data-plane methods (`subscribe_events`, `query_events`, `list_artifacts`) target `data_plane_url`. When `data_plane_url` is absent, all methods resolve against `url` — zero config change for S1–S3.

Data plane config in `monet.toml`:

```toml
[planes]
data_url = "https://data.customer.com"

[planes.progress]
backend = "postgres"
dsn     = "postgresql://..."
```

`ProgressBackend` enum rejects invalid values at config-parse time. Boot validator raises `ConfigurationError` if `data_plane_url` is set but no `ProgressWriter` backend is configured.

Data-plane SSE stream emits `id: <event_id>` on each event. Browser `EventSource` reconnects automatically via `Last-Event-ID`. Python client tracks last `event_id` and reconnects with `?after=<last_event_id>`.

The SaaS platform itself — accounts, billing, quotas, customer UI — lives in a **separate downstream repo that imports `monet`**. Out of scope for this repo. The auth boundary: SaaS repo mounts `monet`'s server factory and injects its own auth/tenant resolver.

Key files: `src/monet/server/__init__.py` (three app constructors), `src/monet/progress/`, `src/monet/client/__init__.py` (dual-view), `src/monet/config/_schema.py` (`PlanesConfig`).

## S6 — Library / embedded (no server) — REMOVED

Previously: `from monet import run; asyncio.run(run(topic))` with a `MemorySaver` checkpointer, no Docker.

Removed in the client-decoupling refactor when `src/monet/_run.py` was deleted. `src/monet/__main__.py` was deleted alongside to remove the last live `MemorySaver` path. Library callers now use `monet dev` + `MonetClient`, or shell to `aegra dev` directly.

Trigger for reintroduction: a concrete need for library-only usage (notebook example, or a CLI subcommand that must avoid Docker). Tracked in `CLAUDE.md ## Deferred from client-decoupling refactor` and `## Roadmap` under Lower priority / triggered. If reintroduced, the driver should consume the default pipeline adapter rather than duplicating composition logic.

## Scenario × capability matrix

| Scenario | Server | Workers | Queue | Auth | Status |
|---|---|---|---|---|---|
| S1 local dev | `monet dev` (Docker) | In-server (`pool="local"`) | `memory` or `sqlite` | None | Supported |
| S2 self-hosted prod | `aegra serve` | `monet worker --server-url` | `redis` or `upstash` | `MONET_API_KEY` | Supported — `examples/deployed/` |
| S3 split fleet | `aegra serve` | N × `monet worker` per pool; or cloud dispatch | `redis` or `upstash` | `MONET_API_KEY` | Supported — pull + ECS/Cloud Run dispatch — `examples/split-fleet/` |
| S4 workers-only | — | `monet worker` (no `--server-url`) | `memory` | — | Test/library only |
| S5 split-plane / SaaS | Vendor `create_control_app` + customer `create_data_app` | Customer-hosted `monet worker` | Shared `redis` / `upstash` | `MONET_API_KEY`; pluggable auth in downstream SaaS repo | Supported — three app constructors + dual-view client |
| S6 embedded | — | — | — | — | Removed (see Deferred) |

## Progressive adoption path

The deployment scenarios above are not just options — they form a trust gradient. Each step is pulled by demonstrated success, not pushed by sales or assumption. Trust and blast radius grow together.

### Step 1 — Personal worker (S1)

Developer runs `monet dev` on their laptop. Agents run in hardened containers on the same machine. Blast radius: one person's laptop. Data never leaves. Entry cost: zero infrastructure.

The developer builds a track record — weeks of OTel traces showing every tool call, every HITL decision, every artifact. This is the same risk profile as running the agent directly, but with structural safety and evidence accumulation.

### Step 2 — Team workers (S2)

Multiple developers, each running `monet worker` on their own machines, connecting to a shared server. Each person's worker only processes tasks routed to their pool. Blast radius: individual machine scope.

The shared server sees pointers and skeletons only. A Langfuse dashboard shows every agent's track record side by side. Trust is no longer self-reported — it's observable by the team.

### Step 3 — Orchestration SaaS (S5)

Control plane hosted (self-hosted or vendor-hosted). Data plane stays on customer machines. Customer points OTel at their own Langfuse/Datadog/Splunk. Enterprise IT can audit without touching the control plane.

The governance-containment gap disappears — you can both monitor and stop agents, because abort is a control-plane operation that kills the worker-side container.

### Step 4 — Centralized fleet (S3)

Track record justifies moving workers off laptops onto VPS or cloud. Push pools (ECS/Cloud Run dispatch) for centralized management. Security team manages the worker fleet with their preferred policy engine (Microsoft AGT, OPA, Cedar).

Individual users submit work through the control plane. Blast radius is managed infrastructure with proper IAM, network boundaries, and fleet-level policy.

### Transitions are configuration changes

Each step is a config change, not a migration. Same code, same pipelines, same agents:

- S1 → S2: add `MONET_SERVER_URL` to worker
- S2 → S5: deploy server with `--plane control`, point workers at it
- S5 → S3: add `dispatch = "ecs"` to `monet.toml`

No rewrites at any step. The pipeline topology is the constant.

## Deployment-assumption defaults

- `MONET_SERVER_URL` default `http://localhost:2026` across `cli/_run.py`, `cli/_chat.py`, `cli/_runs.py`, `cli/_worker.py`, `config/_schema.py`. All env-overridable for remote deployment.
- `MONET_QUEUE_BACKEND` default `memory`; Redis URI default `redis://localhost:6379`. All env-overridable.
- `MONET_DATA_PLANE_URL` — when set, `MonetClient` routes telemetry methods to the data plane. Absent = unified URL for all methods.
- `MONET_TASK_LEASE_TTL` — lease window in seconds (default 90). Tasks not heartbeated within this window are re-queued.
- `MONET_TASK_LEASE_INTERVAL` — worker heartbeat interval in seconds (default 30).
- `cli/_dev.py` healthcheck polls `127.0.0.1` — dev-only, correct.

No hardcodes that break remote deployment.
