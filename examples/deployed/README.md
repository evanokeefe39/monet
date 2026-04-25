# Deployed (Railway + self-host)

Deploy monet as **two services** — an Aegra-hosted server and one or
more remote workers — wired together via a shared Postgres
(checkpointing), Redis (task queue), and `MONET_API_KEY` (auth).

This is scenario S2 from `docs/architecture/deployment-scenarios.md`:
self-hosted production, single tenant.

## Why two services

- **server/** runs `aegra serve` — graph execution (entry / planning /
  execution / chat) plus monet's worker-coordination routes (register,
  heartbeat, claim). Stateless apart from Postgres.
- **worker/** runs `monet worker --server-url ... --pool default` — claims
  tasks from the shared Redis queue, executes the reference agents
  (`planner`, `researcher`, `writer`, `qa`, `publisher`), posts results
  back.

They never talk directly. All coordination goes through Postgres (thread
state) and Redis (the task queue). Scale workers horizontally by
deploying the `worker/` service more times.

## Layout

```
examples/deployed/
  .env.example                    # shared env for both services
  docker-compose.split-plane.yml  # one-time S5 split-plane validation
  server/                         # aegra serve
    aegra.json
    server_graphs.py
    railway.toml
    docker-compose.yml            # includes worker service for local S2 testing
    Dockerfile
    pyproject.toml
  worker/                         # monet worker
    agents/__init__.py  # `import monet.agents` — registers reference set
    railway.toml
    Dockerfile
    pyproject.toml
  railway/                        # Railway deployment guide + reference configs
    server.toml
    worker.toml
    README.md
```

## Run locally with Docker Compose

```bash
cd examples/deployed
cp .env.example .env
# Fill in GEMINI_API_KEY, GROQ_API_KEY, pick a MONET_API_KEY secret.

cd server
docker compose up -d
```

Compose brings up Postgres, Redis, the server, and a worker together.

Quick sanity check:

```bash
curl http://localhost:2026/health
```

Then from another terminal (outside Docker):

```bash
export MONET_API_KEY="<same as .env>"
monet run --url http://localhost:2026 "AI trends in healthcare"
```

## Deploy to Railway

1. Fork this repo or push to your own.
2. Create a new project on [Railway](https://railway.com).
3. Add the **Postgres** plugin (or connect a Neon database).
4. Add the **Redis** plugin (or connect Upstash).
5. Create the **server** service: connect the repo, set root directory
   to `examples/deployed/server`. Railway reads `railway.toml` and runs
   `aegra serve`.
6. Create the **worker** service: same repo, root directory
   `examples/deployed/worker`. Set `MONET_SERVER_URL` to the server
   service's public URL.
7. Set shared env vars on both services: `MONET_API_KEY`,
   `MONET_QUEUE_BACKEND=redis`, `REDIS_URI` (from the plugin, Upstash
   TCP string, or any Redis 7+ endpoint), `GEMINI_API_KEY`,
   `GROQ_API_KEY`. Server also needs `DATABASE_URL` from the Postgres
   plugin.
8. Deploy.

## Pool names

The worker claims `--pool default` (via `MONET_WORKER_POOL`). If your
agents declare a different pool name, set `MONET_WORKER_POOL` on the
worker service — worker and agent pool names must agree.

## Connect from your machine

```bash
export MONET_API_KEY="your-secret"
monet run --url https://your-server-service.up.railway.app "AI trends in healthcare"
```

Or with the Python client:

```python
from monet.client import MonetClient
from monet.pipelines.default import run as run_default

client = MonetClient(url="https://your-server-service.up.railway.app")
async for event in run_default(client, "AI trends in healthcare", auto_approve=True):
    print(event)
```

## Bring your own infrastructure

Swap any managed service by changing the connection string:

- **Postgres**: Railway plugin, Neon, Supabase, RDS, any Postgres 14+
- **Redis**: Railway plugin, Upstash, ElastiCache, any Redis 7+
- **Tracing**: Langfuse Cloud, self-hosted Langfuse, any OTLP endpoint

## Split-plane validation (S5, one-time)

`docker-compose.split-plane.yml` runs control plane (orchestration) and
data plane (events/SSE) as separate server processes to validate that
control → data routing works end-to-end.

```bash
cd examples/deployed
docker compose -f docker-compose.split-plane.yml up

# In another terminal:
export MONET_API_KEY="<same as .env>"
monet run "test topic" \
  --url http://localhost:2026 \
  --data-plane-url http://localhost:3000 \
  --auto-approve
```

Tear it down after validation — day-to-day dev uses the unified server.

## Other setups

- [quickstart](../quickstart/) — minimal laptop setup
- [local](../local/) — Docker Compose with Postgres + Langfuse
- [split-fleet](../split-fleet/) — multiple worker pools (S3)
