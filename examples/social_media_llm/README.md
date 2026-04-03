# Social Media Content Generation — LLM Example

Real LLM-backed agents using the monet SDK for social media content generation
across Twitter, LinkedIn, and Instagram.

## What This Demonstrates

- **Real LLM integration**: Gemini Flash (planner, researcher, writer), Groq (QA)
- **Tool calling**: Researcher uses Tavily web search via `create_react_agent`
- **CLI agent pattern**: Publisher wraps a subprocess emitting the event vocabulary
- **All SDK helpers**: emit_progress, write_artifact, emit_signal, get_run_context,
  get_run_logger, handle_agent_event, NeedsHumanReview, EscalationRequired, SemanticError
- **Non-fatal signals**: QA emits `needs_human_review` signal for marginal confidence
  while still returning a verdict (demonstrates `emit_signal()` vs exceptions)
- **Same graph topology** as the stub example — proves the registry-based agent swap

## Prerequisites

```bash
# Install LLM dependencies
uv sync --group dev --group llm-examples

# Set API keys (or load from ~/repos/deepagents-sandpit/.env)
export GEMINI_API_KEY=...
export GROQ_API_KEY=...
export TAVILY_API_KEY=...
```

## Services (Logging / Tracing)

Start the dev services from the repo root before running the example if you want tracing:

```bash
docker compose -f docker-compose.dev.yml up -d
```

This brings up Langfuse at http://localhost:3000 (plus Postgres, ClickHouse, Redis, MinIO). Create a project in Langfuse and add the keys to your `.env`:

```
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=http://localhost:3000
```

## Quick Start

```bash
# Run the interactive CLI
uv run python examples/social_media_llm/run_cli.py

# With a specific topic
uv run python examples/social_media_llm/run_cli.py AI trends 2026

# Run unit tests (mocked LLM, no API keys needed)
uv run pytest examples/social_media_llm/ -v -m "not llm_integration"

# Run integration tests (real LLM, requires API keys)
uv run pytest examples/social_media_llm/ -v -m llm_integration
```

## Architecture

```
agents/
  __init__.py (thin @agent wrappers — monet envelope)
    +-- planner.py     (Gemini Flash — triage + work brief)
    +-- researcher.py  (Gemini Flash + Tavily — create_react_agent)
    +-- writer.py      (Gemini Flash — platform-specific content)
    +-- qa.py          (Groq llama-3.3-70b — fast quality evaluation)
    +-- publisher.py   (subprocess -> publisher_cli.py)
        +-- publisher_cli.py  (emits event vocabulary to stdout)

graphs/
  entry.py     — triage and routing
  planning.py  — iterative plan with HITL approval
  execution.py — wave-based parallel execution with QA reflection
```

The graph files (state.py, graphs/) are identical in topology to the stub
example. Only the agent registrations differ.

## SDK Helper Coverage

| Agent | emit_progress | write_artifact | get_run_context | get_run_logger | emit_signal | NeedsHumanReview | EscalationRequired | SemanticError |
|-------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| planner/fast | x | | | x | | | | |
| planner/plan | x | x | | | | x | | |
| researcher/deep | x | x | x | | | | | |
| writer/deep | x | x | | | | | | |
| qa/fast | x | | | | x | | | x |
| publisher/publish | x | | | x | | | x | |

## File Structure

```
social_media_llm/
  state.py              — shared state schemas (Entry, Planning, Execution)
  run.py                — top-level orchestrator sequencing the three graphs
  cli.py                — interactive terminal client with env loading
  run_cli.py            — convenience entry point (no PYTHONPATH needed)
  agents/
    __init__.py         — thin @agent wrappers (SDK envelope translation)
    planner.py          — pure LangChain planner (zero monet imports)
    researcher.py       — pure LangChain researcher + Tavily (zero monet imports)
    writer.py           — pure LangChain writer (zero monet imports)
    qa.py               — pure LangChain QA with Groq (zero monet imports)
    publisher.py        — subprocess launcher (zero monet imports)
    publisher_cli.py    — standalone CLI emitting event vocabulary
  graphs/
    __init__.py         — re-exports build_*_graph functions
    entry.py            — entry/triage graph
    planning.py         — planning graph with HITL approval
    execution.py        — execution graph with wave-based parallelism
  tests/
    test_agents.py      — unit tests (mocked) + integration tests (real LLM)
```
