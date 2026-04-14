# Deployment & Run Scenarios

Six shapes the codebase supports, with the wiring each one relies on. S1–S3 are user-facing, S4 is a dev-loop detail, S5 is the SaaS shape, S6 is removed but often asked for.

## S1 — Local all-in-one (dev laptop)

One machine. `monet dev` starts Aegra plus Postgres and Redis in Docker. Worker pool is in-process (`pool="local"` runs as a background task in the server process). User invokes `monet run <topic>` or `monet chat`. `MonetClient` points at `http://localhost:2026`.

Target audience: tutorials, `examples/quickstart`, `examples/local`, `examples/custom-graph`.

Key files: `src/monet/cli/_dev.py`, `src/monet/server/_langgraph_config.py`, `src/monet/cli/_run.py`.

## S2 — Self-hosted production (single tenant)

User runs `aegra serve` plus managed Postgres plus managed Redis on their own infra. Workers run as separate processes or containers: `monet worker --server-url https://monet.internal --pool <p>`. Queue backend is Redis or Upstash.

Single `MONET_API_KEY` shared by workers and clients. `MonetClient(url=...)` from the user's app or CI.

Target audience: `examples/deployed`.

Key files: `src/monet/cli/_worker.py`, `src/monet/server/_auth.py`, `src/monet/queue/backends/redis.py`.

## S3 — Split fleet (same customer, multiple worker pools)

Same as S2 but N worker processes across regions or hardware classes, each claiming by `--pool` name declared in `monet.toml [pools]`. One logical server, several worker fleets.

Push pools (Cloud Run, Lambda, Vercel Functions) are declared in config but the dispatcher is **not implemented** — see `CLAUDE.md ## Roadmap` Priority 2.

Key files: `src/monet/config/_schema.py` (pool parsing), `src/monet/orchestration/_invoke.py` (queue dispatch; push branch missing).

## S4 — Workers-only local (test / library)

`monet worker` without `--server-url` uses `InMemoryTaskQueue` in-process. Exists today at `src/monet/cli/_worker.py`. Used by the test suite via the `_queue_worker` autouse fixture in `tests/conftest.py`.

Not a user-facing run mode — no pipeline composition happens here, no graph driver. Execution plane only. Useful for validating `@agent` + signals + artifact store in isolation.

## S5 — Vendor-hosted orchestrator, customer-hosted workers (SaaS)

The SaaS platform itself — user management, accounts, billing, usage limits, customer UI — lives in a **separate downstream repo that imports `monet`**. This keeps the SDK repo focused and MIT-licensed, and isolates fast-churning commercial concerns from orchestration correctness.

The vendor deployment from the outside: Aegra + Postgres + shared Redis/Upstash running on vendor infra behind a URL like `https://api.monet.cloud`. Customers run `monet worker --server-url <vendor-url> --pool <tenant-pool>` outbound-only. `MonetClient` also targets the vendor URL.

`monet`'s job for S5 is only to **expose the enabling primitives** the downstream SaaS repo builds on. Out of scope for this repo: user accounts, billing, quota UX, rate-limit dashboards, API-key issuance/rotation flows, customer onboarding.

**Queue plane already works** — every backend is pull/poll, no inbound callbacks to workers. See `src/monet/queue/backends/`.

**Control plane enabling primitives not yet present** — single global `MONET_API_KEY` at `src/monet/server/_auth.py`, no tenant/workspace/project ID in request path, state, or `MonetClient.list_runs()`. These are the primitives the SaaS repo needs `monet` to surface. See `CLAUDE.md ## Roadmap` Priority 1 for the five extension points.

The boundary is the auth dependency swap: SaaS repo ships a FastAPI app that mounts `monet`'s server factory and injects its own auth/tenant resolver. Everything downstream of that point is `monet`; everything upstream is SaaS.

## S6 — Library / embedded (no server) — REMOVED

Previously: `from monet import run; asyncio.run(run(topic))` with a `MemorySaver` checkpointer, no Docker.

Removed in the client-decoupling refactor when `src/monet/_run.py` was deleted. `src/monet/__main__.py` was deleted alongside to remove the last live `MemorySaver` path. Library callers now use `monet dev` + `MonetClient`, or shell to `aegra dev` directly.

Trigger for reintroduction: a concrete need for library-only usage (notebook example, or a CLI subcommand that must avoid Docker). Tracked in `CLAUDE.md ## Deferred from client-decoupling refactor` and `## Roadmap` under Lower priority / triggered. If reintroduced, the driver should consume the default pipeline adapter rather than duplicating composition logic.

## Scenario × capability matrix

| Scenario | Server | Workers | Queue | Auth | Status |
|---|---|---|---|---|---|
| S1 local dev | `monet dev` (Docker) | In-server (`pool="local"`) | `memory` or `sqlite` | None | Supported |
| S2 self-hosted prod | `aegra serve` | `monet worker --server-url` | `redis` or `upstash` | `MONET_API_KEY` | Supported |
| S3 split fleet | `aegra serve` | N × `monet worker`, different `--pool` | `redis` or `upstash` | `MONET_API_KEY` | Supported except push pools |
| S4 workers-only | — | `monet worker` (no `--server-url`) | `memory` | — | Test/library only |
| S5 SaaS | Vendor-hosted Aegra | Customer-hosted `monet worker` | Shared `redis` / `upstash` | Needs pluggable auth + tenant ID | Queue plane ready, control plane pending (see Roadmap P1) |
| S6 embedded | — | — | — | — | Removed (see Deferred) |

## Deployment-assumption defaults

- `MONET_SERVER_URL` default `http://localhost:2026` across `cli/_run.py`, `cli/_chat.py`, `cli/_runs.py`, `cli/_worker.py`, `config/_schema.py`. All env-overridable for remote deployment.
- `MONET_QUEUE_BACKEND` default `memory`; Redis URI default `redis://localhost:6379`. All env-overridable.
- `cli/_dev.py` healthcheck polls `127.0.0.1` — dev-only, correct.

No hardcodes that break remote deployment.
