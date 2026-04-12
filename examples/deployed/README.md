# Deployed (Railway)

Deploy monet to Railway with managed infrastructure.

## Services

| Component | Provider | Notes |
|-----------|----------|-------|
| Aegra (monet) | Railway | Configured by `railway.toml` — graph execution + worker routes |
| Postgres | Railway plugin / Neon | Checkpointing, thread state, deployments |
| Redis | Railway plugin / Upstash | Task queue (optional — only for distributed workers) |
| Tracing | Langfuse Cloud | Optional observability |

## Deploy

1. Fork this repo (or push to your own)
2. Create a new project on [Railway](https://railway.com)
3. Connect your repo, set the root directory to `examples/deployed`
4. Add a **Postgres** plugin (or connect a Neon database)
5. Set environment variables from `.env.example`
6. Deploy

Railway reads `railway.toml` and starts `aegra serve` automatically.
Aegra serves both the graph execution API and monet's worker/task
management routes on a single port.

## Docker Compose

For self-hosted deployment, use the included `docker-compose.yml`:

```bash
cd examples/deployed
cp .env.example .env     # fill in API keys
docker compose up
```

## Bring your own infrastructure

Swap any managed service by changing the connection string:

- **Postgres**: Railway plugin, Neon, Supabase, RDS, any Postgres 14+
- **Redis**: Railway plugin, Upstash, ElastiCache, any Redis 7+
- **Tracing**: Langfuse Cloud, self-hosted Langfuse, or any OTLP endpoint

## Connect from your machine

```bash
export MONET_API_KEY="your-secret"
monet run --url https://your-app.up.railway.app "AI trends in healthcare"
```

Or use the Python client directly:

```python
from monet.client import MonetClient

client = MonetClient(url="https://your-app.up.railway.app")
async for event in client.run("AI trends in healthcare"):
    print(event)
```

## Other setups

- [quickstart](../quickstart/) — zero infrastructure
- [local](../local/) — Docker Compose with Postgres and Langfuse
