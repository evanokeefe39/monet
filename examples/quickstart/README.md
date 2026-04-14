# Quickstart

Run the monet content workflow locally.

## Prerequisites

- **Docker Desktop** — `monet dev` uses Aegra, which auto-starts PostgreSQL
  in a Docker container. Install from https://www.docker.com/products/docker-desktop/
  and make sure it's running before you start.
- **Python 3.12+** and **uv**

## Setup

```bash
cd examples/quickstart
uv sync
cp .env.example .env
# Fill in at least one LLM provider key
```

## Run

**Terminal 1 — start the server:**

```bash
monet dev
```

**Terminal 2 — run a topic:**

```bash
monet run "AI trends in healthcare"
```

## Auto-approve mode

```bash
monet run "AI trends in healthcare" --auto-approve
```

## Custom graphs

Drop an `aegra.json` (or `langgraph.json`) in this directory to add or
override graphs. `monet dev` merges your graphs with monet's defaults
(entry, planning, execution).

## Programmatic usage

Start `monet dev` in another terminal, then drive the server from Python:

```python
import asyncio
from monet.client import MonetClient
from monet.pipelines.default import run as run_default

async def main() -> None:
    client = MonetClient()
    async for event in run_default(client, "AI trends in healthcare", auto_approve=True):
        print(event)

asyncio.run(main())
```

## Next steps

- [local](../local/) — Docker Compose with Postgres and Langfuse
- [deployed](../deployed/) — Railway with managed infrastructure
