# Social Media Content Generation Example

Demonstrates the monet three-graph supervisor topology with an interactive
terminal client. All agents are stubs (no LLM calls). The workflow generates
social media content for Twitter, LinkedIn, and Instagram.

## What This Demonstrates

- **Three-graph supervisor topology**: entry (triage) -> planning (HITL approval) -> execution (wave-based)
- **Send API**: wave-level parallelism — 3 writer agents run concurrently in a single wave
- **HITL interrupts**: approve, reject, or reject-with-feedback at the planning gate
- **emit_progress()**: intra-node streaming via LangGraph's get_stream_writer()
- **Signal propagation**: typed exceptions translate to routing decisions
- **Lean state**: only summaries and pointers, never full content in graph state
- **QA reflection**: post-wave quality gate (jidoka pattern)
- **Deterministic routing**: all routing functions read structured state, no LLM in the orchestrator

## Quick Start

```bash
# Run the interactive CLI
uv run python examples/social_media_content/run_cli.py

# Or with PYTHONPATH set explicitly
PYTHONPATH=. uv run python -m examples.social_media_content

# Run automated tests
uv run pytest examples/social_media_content/test_social_media.py -v -m "not integration"
```

## Interactive CLI

The CLI walks through the full workflow:

1. Enter a content topic (or press Enter for "AI in marketing")
2. Triage classifies the request as complex
3. Planner produces a work brief
4. You approve, reject, or provide feedback
5. If feedback: planner revises (picks from 3 prewritten variants)
6. Execution runs wave-by-wave with streaming progress
7. QA reflection after each wave
8. Final summary with all results

## Architecture

```
Entry Graph          Planning Graph              Execution Graph
-----------          --------------              ---------------
triage_node   --->   planner_node   --->  load_plan
                       |    ^              |
                       v    |         prepare_wave --[Send]--> agent_node (x N)
                    research_node         |                        |
                       |              collect_wave <---------------+
                       v                  |
                  human_approval    wave_reflection
                  (interrupt)             |
                                      advance / human_interrupt
                                          |
                                        END
```

## Observability with Langfuse

Start the dev infrastructure:

```bash
docker compose -f docker-compose.dev.yml up -d
```

Run the CLI with tracing enabled:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run python -m examples.social_media_content
```

Open Langfuse at http://localhost:3000 (first run requires creating an account).

### What to Look For in Langfuse

- **Trace view**: single trace spanning all three graphs, with spans for each agent invocation
- **Agent spans**: tagged with `gen_ai.agent.id`, `gen_ai.agent.command`, `monet.run_id`
- **Parallel execution**: wave 2 writer spans overlapping in time (proves Send parallelism)
- **Timing differences**: `fast` commands ~0.1s, `deep` commands ~0.5s visible in span durations
- **Signal propagation**: spans showing `needs_human_review` attribute when QA flags issues

## Postgres Integration Test

The integration test uses a real Postgres checkpointer instead of MemorySaver:

```bash
docker compose -f docker-compose.dev.yml up -d
uv run pytest examples/social_media_content/test_social_media.py -v -m integration
```

Requires `psycopg` and a running Postgres instance. Set `MONET_POSTGRES_URL` to
override the default connection string.

## File Structure

| File | Purpose |
|------|---------|
| `agents.py` | 7 stub agents with mock responses and artificial delays |
| `state.py` | TypedDict state schemas for all three graphs |
| `entry_graph.py` | Triage graph (single node) |
| `planning_graph.py` | Plan construction + HITL approval loop |
| `execution_graph.py` | Wave-based execution with Send, QA reflection |
| `run.py` | Top-level orchestrator sequencing the three graphs |
| `cli.py` | Interactive terminal client with streaming |
| `test_social_media.py` | 12 automated tests (11 unit + 1 integration) |
