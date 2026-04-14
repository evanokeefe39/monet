# Split Fleet — multiple worker pools

Deploy monet with **two worker pools** (`fast` + `heavy`) claiming from
the same server. A `fast_agent` runs on the `fast` pool and returns
immediately; a `heavy_agent` runs on the `heavy` pool and simulates a
5-second compute. Fast tasks never block on heavy ones because each
pool drains independently.

This is scenario S3 from `docs/architecture/deployment-scenarios.md`:
same server, N worker fleets.

## Why split pools

Real workloads mix latency profiles — interactive chat responses and
hour-long batch jobs. Running both on one worker fleet means the
latency-sensitive work waits behind long jobs. Splitting pools lets
each fleet size and scale according to its workload.

## Layout

```
examples/split-fleet/
  agents/
    __init__.py         # imports both agent modules
    fast_agent.py       # @agent(pool="fast")
    heavy_agent.py      # @agent(pool="heavy")
  graphs/
    demo_graph.py       # fan-out graph: both agents in parallel
  monet.toml            # declares pools + "demo" entrypoint
  .env.example
  pyproject.toml
  compose/              # Docker Compose variant
  railway/              # Railway variant
```

## Run locally with Docker Compose

```bash
cd examples/split-fleet
cp .env.example .env
# Fill in MONET_API_KEY with any random secret.

cd compose
docker compose up -d --build
```

Five services come up: postgres, redis, monet (server), worker-fast,
worker-heavy.

Confirm workers registered:

```bash
docker compose logs worker-fast worker-heavy | grep 'registered'
```

Drive a fan-out run. Run this from `examples/split-fleet/` so the
`monet.toml` entrypoint declaration is picked up:

```bash
cd examples/split-fleet            # monet.toml lives here
export MONET_API_KEY="<same as .env>"
monet run --url http://localhost:2026 --graph demo "split-fleet smoke test"
```

You should see the fast result return immediately; the heavy result
returns after ~5 seconds. Workers log their claims:

```bash
docker compose logs worker-fast worker-heavy --tail 20
```

## Deploy to Railway

See [railway/README.md](railway/README.md) for the three-service Railway
walkthrough (one server, one worker-fast, one worker-heavy — each with
its own service root in the dashboard).

## Pool naming

Pool names are free-form strings. `monet.toml [pools.<name>]` is
self-documentation today — the queue accepts any pool name that a
worker claims, whether declared or not. Declaring pools here is useful
once server-side pool-claim validation lands (see
`CLAUDE.md ## Roadmap` Priority 1).

## Other setups

- [quickstart](../quickstart/) — minimal laptop setup (S1)
- [local](../local/) — Docker Compose with Postgres + Langfuse (S1)
- [deployed](../deployed/) — single-pool server + worker (S2)
