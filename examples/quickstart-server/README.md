# Quickstart (server)

Run the monet content workflow as a client/server pair.

For a simpler single-process demo, see
[quickstart-local](../quickstart-local/).

## Setup

```bash
cd examples/quickstart-server
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

## Auto-approve mode (no prompts)

```bash
monet run "AI trends in healthcare" --auto-approve
```

## Custom graphs

Drop a `langgraph.json` in this directory to add or override graphs.
`monet dev` merges your graphs with monet's defaults (entry, planning, execution).

## Next steps

- [deployed](../deployed/) — Docker Compose with Postgres and Langfuse
