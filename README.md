# monet

## Installation

```bash
pip install monet
```

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run mypy src/
```

## Services (Logging / Tracing)

The dev stack includes Langfuse (tracing UI), Postgres, ClickHouse, Redis, and MinIO:

```bash
docker compose -f docker-compose.dev.yml up -d
```

Once running, Langfuse is available at http://localhost:3000. Create a project and grab API keys to configure tracing in your `.env`:

```
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=http://localhost:3000
```

To stop the stack:

```bash
docker compose -f docker-compose.dev.yml down
```

To stop and remove all data:

```bash
docker compose -f docker-compose.dev.yml down -v
```

## License

MIT
