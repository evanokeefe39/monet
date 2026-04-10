# Quickstart

Run the monet content workflow with zero infrastructure.

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

Drop a `langgraph.json` in this directory to add or override graphs.
`monet dev` merges your graphs with monet's defaults (entry, planning, execution).

## Programmatic usage

Skip the CLI and drive monet from Python directly:

```python
import asyncio
from monet import run

async def main() -> None:
    async for event in run("AI trends in healthcare"):
        print(event)

asyncio.run(main())
```

## Next steps

- [local](../local/) — Docker Compose with Postgres and Langfuse
- [deployed](../deployed/) — Railway with managed infrastructure
