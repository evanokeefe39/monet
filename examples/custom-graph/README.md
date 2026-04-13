# Custom Graph + Hooks

Demonstrates building a custom graph with hook points, BYO agents, and
worker-side hooks — all composing alongside monet's built-in graphs.

## What this example shows

- **BYO agents** (`agents/summarizer.py`): a custom agent using `@agent`
- **Worker hooks** (`hooks/`): `@on_hook("before_agent")` injects tone
  config, `@on_hook("after_agent")` validates output
- **Custom graph** (`graphs/review_pipeline.py`): a `StateGraph` with its
  own `before_review` / `after_review` hook points via `GraphHookRegistry`
- **Composition** (`aegra.json`): the review graph appears alongside
  monet's entry, planning, and execution graphs when you run `monet dev`

## Structure

```
agents/
  summarizer.py           # @agent("summarizer") — BYO agent
hooks/
  context_injection.py    # @on_hook("before_agent") — inject tone
  output_validation.py    # @on_hook("after_agent") — validate output
graphs/
  review_pipeline.py      # Custom StateGraph with graph hook points
config/
  tone.md                 # Plain text read by the tone injection hook
aegra.json                # Points review graph into monet dev
```

## Prerequisites

- **Docker Desktop** — `monet dev` uses Aegra, which auto-starts PostgreSQL
  in a Docker container. Make sure Docker Desktop is running before you start.
- **Python 3.12+** and **uv**

## Setup

```bash
cd examples/custom-graph
uv sync
cp .env.example .env     # if needed
```

## Run

```bash
monet dev
```

The review graph appears at `http://127.0.0.1:2026` alongside the
standard entry, planning, and execution graphs.

## How it works

**Worker process**: when `monet worker` imports `agents/summarizer.py`,
the `@agent` decorator registers the handler. Importing `hooks/*.py`
registers the `@on_hook` handlers. Both happen at import time via
module-level singletons — no app object needed.

**Server process**: `monet dev` reads `aegra.json` (or `langgraph.json`),
merges the review graph with monet's defaults, and starts the Aegra dev
server. The review graph accepts an optional `GraphHookRegistry` for
server-side hook points that graph operators can wire in.

**Separation**: worker hooks (before_agent, after_agent) run in the
worker process. Graph hooks (before_review, after_review) run in the
server process. The boundary is enforced by which process imports what.
