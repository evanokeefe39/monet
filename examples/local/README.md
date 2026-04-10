# Local (Docker Compose)

Run monet with Postgres checkpointing and optional Langfuse tracing,
all via Docker Compose.

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

## Minimal (Postgres only)

```bash
docker compose up -d
```

This starts Postgres for checkpointing. Run monet as usual:

```bash
monet dev                                        # terminal 1
monet run "AI trends in healthcare"              # terminal 2
```

## Full observability (Postgres + Langfuse)

```bash
docker compose --profile tracing up -d
```

This adds ClickHouse, MinIO, Redis, and Langfuse. The tracing stack
uses ~1.6 GB by default. Adjust `mem_limit` values in
docker-compose.yml if needed.

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
docker compose --profile tracing down -v
```

## Other setups

- [quickstart](../quickstart/) — zero infrastructure
- [deployed](../deployed/) — Railway with managed infrastructure
