# Local (Docker Compose)

Run monet with optional Langfuse tracing. Postgres checkpointing is
provisioned automatically by `monet dev` — no manual compose step needed.

## Prerequisites

- Docker and Docker Compose
- uv
- API keys: `GEMINI_API_KEY`, `GROQ_API_KEY`

## Setup

```bash
cd examples/local
uv sync
cp .env.example .env    # fill in API keys
```

## Minimal

```bash
monet dev                                        # terminal 1
monet run "AI trends in manufacturing"           # terminal 2
```

`monet dev` auto-starts Postgres on `:5432` via its own
`.monet/docker-compose.yml`. No other services needed.

## Full observability (Langfuse)

Start the tracing stack separately — it does **not** include Postgres
(that comes from `monet dev`):

```bash
docker compose --profile tracing up -d
```

This adds ClickHouse, MinIO, Redis, a dedicated tracing Postgres, and
Langfuse. The tracing stack uses ~1.6 GB by default. Adjust
`mem_limit` values in `docker-compose.yml` if needed.

### Langfuse first-time setup

1. Open http://localhost:3000
2. Create an organization and project
3. Settings > API Keys > create a key pair
4. Add `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` to `.env`

Then run monet:

```bash
monet dev                                        # terminal 1
monet run "AI trends in healthcare"              # terminal 2
```

Open http://localhost:3000 to view traces.

## Tear down

```bash
monet dev down                                    # stops aegra postgres
docker compose --profile tracing down -v          # stops tracing stack
```

## Other setups

- [quickstart](../quickstart/) — minimal setup (S1)
- [deployed](../deployed/) — server + worker on Railway / self-host (S2)
- [split-fleet](../split-fleet/) — multiple worker pools (S3)
