# Quickstart

Run the monet content workflow end-to-end: triage a topic, plan the
work, then execute with the built-in agents (researcher, writer, QA,
publisher).

## Setup

```bash
cd examples/quickstart
uv sync
```

Create a `.env` file with your API keys:

```bash
GEMINI_API_KEY=...   # planner, researcher, writer, publisher
GROQ_API_KEY=...     # QA agent
# Optional — enables web search in researcher/deep:
# TAVILY_API_KEY=...
```

## Client/server mode

Two terminals. The server hosts the three LangGraph graphs; the client
drives the workflow via `monet.client`.

**Terminal 1 — start the graph server:**

```bash
cd examples/quickstart
uv run langgraph dev
```

The server starts on `http://localhost:2024`.

**Terminal 2 — run the client:**

```bash
cd examples/quickstart
uv run python client.py "AI trends in healthcare"
```

The client connects to the server, runs triage → planning (auto-approved)
→ execution, and prints results with artifact previews and QA verdicts.

## In-process mode

No server needed. Everything runs in a single process using `bootstrap()`:

```bash
cd examples/quickstart
uv run python -m monet "AI trends in healthcare"
```

## What happens

1. **Triage** — the planner agent classifies the topic as simple or complex
   and suggests which agents to involve.
2. **Planning** — the planner builds a structured work brief with phases
   and waves. In the client example this is auto-approved; in production
   you would inspect the brief and approve, reject, or revise.
3. **Execution** — agents run in parallel waves (researcher → writer →
   publisher), with QA reflection after each wave. Results and artifacts
   are stored in the catalogue.

## Files

| File | Purpose |
|------|---------|
| `server_graphs.py` | Configures monet (tracing, catalogue, queue, worker) and exports the three graph builders for `langgraph dev` |
| `langgraph.json` | Tells the LangGraph server which graphs to serve |
| `client.py` | Drives the three-phase workflow using `monet.client` helpers |
