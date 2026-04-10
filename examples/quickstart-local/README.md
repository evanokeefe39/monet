# Quickstart (local)

Run the monet content workflow in a single process — no server, no
Docker, one terminal.

## Setup

```bash
cd examples/quickstart-local
uv sync
export GEMINI_API_KEY="..."
export GROQ_API_KEY="..."
```

## Run

```bash
uv run python run.py "AI trends in healthcare"
```

## Next steps

- [quickstart-server](../quickstart-server/) — client/server mode
- [deployed](../deployed/) — Docker Compose with Postgres and Langfuse
