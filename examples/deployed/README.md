# Deployed

Run monet with production infrastructure: Postgres checkpointing,
Langfuse tracing, and a containerised LangGraph server.

## Prerequisites

- Docker and Docker Compose
- uv (for the client)
- API keys: `GEMINI_API_KEY`, `GROQ_API_KEY`

## Setup

```bash
cd examples/deployed
cp .env.example .env          # fill in API keys
langgraph build -t monet-deployed .
docker compose up -d
```

### Langfuse first-time setup

1. Open http://localhost:3000
2. Create an organization and project
3. Settings > API Keys > create a key pair
4. Add `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` to `.env`
5. `docker compose restart langgraph-server`

## Run

```bash
uv sync
uv run python client.py "AI trends in healthcare"
```

## Viewing traces

Open http://localhost:3000 after a run completes.

## Production migration

- **Postgres** — swap for managed (RDS, Supabase, Neon)
- **Langfuse** — use Langfuse Cloud or self-hosted production
- **LangGraph server** — push image to a container registry
- **Catalogue** — mount shared volume or implement a cloud backend

## Tear down

```bash
docker compose down -v
```

## Simpler setups

- [quickstart-local](../quickstart-local/) — single process, no infra
- [quickstart-server](../quickstart-server/) — client/server without Docker
