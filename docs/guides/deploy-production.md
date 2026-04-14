# Deploy to production

End-to-end path to a self-hosted production deployment (scenario S2) —
one server, N workers, and a laptop driving `monet run` / `monet chat`
from anywhere on the network.

This guide assumes you've worked through at least `examples/quickstart/`
locally. For the multi-pool variant (S3), see `examples/split-fleet/`.

## Topology

```
┌──────────┐      ┌──────────────────┐      ┌──────────┐
│  laptop  │◄────►│   aegra serve    │◄────►│  worker  │
│ monet chat│     │  monet custom    │      │  pool=default │
│ monet run │     │    routes        │      │ concurrency=20│
└──────────┘      └──────────────────┘      └──────────┘
                         │   │
                    ┌────┘   └────┐
                    ▼             ▼
               ┌────────┐    ┌────────┐
               │Postgres│    │ Redis  │
               └────────┘    └────────┘
```

All coordination flows through Postgres (thread state) and Redis (task
queue). Workers never accept inbound traffic — they poll the server.
Laptop and workers both authenticate with a shared `MONET_API_KEY`.

## Prerequisites

- Managed Postgres 14+ (Neon, Railway plugin, RDS, ...).
- Managed Redis 7+ (Upstash, Railway plugin, ElastiCache, ...).
- A box (VM, Railway service, Fly machine, ...) to run `aegra serve`.
- Zero or more boxes to run `monet worker`. Start with one.
- A shared secret for `MONET_API_KEY` — treat like a DB password.

## 1. Server box

Environment required at boot:

```bash
export MONET_API_KEY="<shared-secret>"
export MONET_DATABASE_URL="postgres://..."     # Aegra checkpoint store
export MONET_REDIS_URI="redis://..."           # queue backend
export MONET_QUEUE_BACKEND=redis
export MONET_DISTRIBUTED=1                     # workers own artifacts
export GEMINI_API_KEY=...                      # or GROQ_API_KEY
```

Copy `examples/deployed/server/` to the box. Its `aegra.json` already
declares the monet auth handler:

```json
{
  "http": {
    "app": "monet.server._aegra_routes:app",
    "enable_custom_route_auth": true
  },
  "auth": {
    "path": "monet.server._aegra_auth:auth"
  }
}
```

This wires `Authorization: Bearer <MONET_API_KEY>` validation to every
route — Aegra's own graph routes (`/threads`, `/runs`, `/assistants`,
`/store`, `/runs/stateless`) and monet's custom routes
(`/api/v1/workers/*`, `/api/v1/deployments`, `/api/v1/runs/*`).

Start:

```bash
aegra serve --port 2026 --host 0.0.0.0
```

Smoke:

```bash
curl https://<server>/health       # 200
curl https://<server>/api/v1/deployments   # 401 without bearer
curl -H "Authorization: Bearer $MONET_API_KEY" \
     https://<server>/api/v1/deployments   # 200, empty list
```

TLS terminates at your reverse proxy (nginx, Caddy, Traefik, Railway's
edge, ...) — monet doesn't ship one.

## 2. Worker box(es)

Environment:

```bash
export MONET_SERVER_URL="https://<server>"
export MONET_API_KEY="<same-shared-secret>"
export MONET_REDIS_URI="<same-redis>"          # if sharing queue
export MONET_QUEUE_BACKEND=redis
export MONET_DISTRIBUTED=1
export MONET_ARTIFACTS_DIR=/var/lib/monet      # local artifact root
export GEMINI_API_KEY=...
```

Copy `examples/deployed/worker/` (or write your own `agents/__init__.py`
that imports the agents you want to expose). Start:

```bash
monet worker --pool default --concurrency 20
```

Concurrency is the semaphore size inside the worker loop — 20 agent
tasks can run in parallel against a single worker process. Also
settable via `MONET_WORKER_CONCURRENCY=20`.

Scale horizontally by starting the command again on more boxes. All
workers with the same `--pool` compete for the same queue shard.

Verify registration from the server box:

```bash
monet status --url https://<server> --api-key $MONET_API_KEY
```

Expected: one `Worker <id>` row per worker process, plus the reference
agents' capabilities.

## 3. Laptop

From anywhere with network reach to the server:

```bash
export MONET_SERVER_URL="https://<server>"
export MONET_API_KEY="<shared-secret>"

monet run "AI trends in healthcare" --auto-approve
monet chat
monet runs list
```

The `MonetClient` threads the API key into the LangGraph SDK as a
`Authorization: Bearer` header. No separate auth step needed — the env
vars above cover `run`, `chat`, `runs list|pending|inspect|resume`, and
`status`.

Bad/missing key returns `401` from the server. Set
`MONET_API_KEY` correctly or pass `--api-key <key>` on the CLI.

## 4. Observability (optional, recommended)

If you have a Langfuse project, add to both server and worker envs:

```bash
export LANGFUSE_PUBLIC_KEY=pk-...
export LANGFUSE_SECRET_KEY=sk-...
export LANGFUSE_HOST=https://cloud.langfuse.com
```

Trace continuity across server → queue → worker is automatic via the
`TRACE_CARRIER_METADATA_KEY` propagation in `monet.client._wire`.

## Scaling and operation

- **More throughput per pool**: raise `--concurrency` per worker until
  CPU or LLM rate-limits saturate, then add more worker boxes.
- **Multiple pools** (e.g. `fast` and `heavy`): declare them in
  `monet.toml [pools]`, run one worker per pool, have agents declare
  their pool via `@agent(pool="heavy")`. See `examples/split-fleet/`.
- **Rotating the API key**: update `MONET_API_KEY` on the server and
  every worker; re-roll laptop envs. No downtime if you run the new
  server alongside the old and migrate laptops, but the simple path is
  restart everything.
- **Secrets hygiene**: workers' `redacted_summary()` boot log shows
  `api_key: set|unset`, never the raw value. Keep it in your secret
  store (Railway/Fly variables, SOPS, Vault), not in git.

## Out of scope here

- Per-user / per-tenant keys → see Roadmap Priority 1 (SaaS primitives).
- Push pools (Cloud Run / Lambda triggers) → Roadmap Priority 2.
- Scheduled runs → Roadmap Priority 3.

## Related

- [Distribution Mode](distribution.md) — pool topology, `monet.toml`
- [Server & Transport](server.md) — what Aegra does under the hood
- [Client SDK](client.md) — `MonetClient` API
- `examples/deployed/` — reference compose + Railway setup
- `examples/split-fleet/` — multi-pool variant
