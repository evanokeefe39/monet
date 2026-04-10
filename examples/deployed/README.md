# Deployed (Railway)

Deploy monet to Railway with managed infrastructure.

## Services

| Component | Provider | Notes |
|-----------|----------|-------|
| monet server | Railway | Configured by `railway.toml` |
| Postgres | Railway plugin / Neon | Checkpointing and state |
| Redis | Railway plugin / Upstash | Task queue (optional for single-worker) |
| Tracing | Langfuse Cloud | Optional observability |

## Deploy

1. Fork this repo (or push to your own)
2. Create a new project on [Railway](https://railway.com)
3. Connect your repo, set the root directory to `examples/deployed`
4. Add a **Postgres** plugin (or connect a Neon database)
5. Set environment variables from `.env.example`
6. Deploy

Railway reads `railway.toml` and starts `monet server` automatically.

## Bring your own infrastructure

The `.env.example` documents each service with provider alternatives.
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
