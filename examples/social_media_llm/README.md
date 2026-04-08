# social_media_llm — reference client for the monet SDK

A minimal interactive terminal client demonstrating how to drive the
monet SDK's built-in reference agents and graphs from a custom CLI.
The educational value of this example is the **client code** in
`cli.py` — how to stream progress events from LangGraph, how to
prompt a human at HITL interrupts, and how to resume after approval
or rejection.

All agents and graphs used here live in the SDK:

- `monet.agents` — planner, researcher, writer, qa, publisher
- `monet.orchestration` — `build_entry_graph`, `build_planning_graph`,
  `build_execution_graph`, and the `EntryState`/`PlanningState`/
  `ExecutionState` TypedDicts

The CLI imports them directly. There is no custom agent or graph code
in this example.

## Install

This example has its own `pyproject.toml` and creates its own `.venv`
inside the example directory, so it never touches the root dev
environment.

```bash
cd examples/social_media_llm
uv sync
```

That pulls in monet (as an editable path dependency on the parent repo)
plus the full reference stack — Gemini, Groq, langchain-community,
tavily-python, exa-py, python-dotenv — everything needed to run the
workflow end to end. Run subsequent commands with `uv run ...` from the
same directory.

If you want a different provider, add it to this example's environment:

```bash
uv add langchain-anthropic   # or langchain-openai, etc.
```

## Environment

Copy the template and fill in your keys — `python-dotenv` loads `.env`
on startup.

```bash
cp .env.example .env
```

```bash
GEMINI_API_KEY=...       # default for planner/researcher/writer/publisher
GROQ_API_KEY=...         # default for qa
EXA_API_KEY=...          # optional — preferred web search for researcher/deep
TAVILY_API_KEY=...       # optional — alternative web search
MONET_CATALOGUE_DIR=.catalogue
```

Model selection is controlled via `MONET_PLANNER_MODEL`,
`MONET_RESEARCHER_MODEL`, `MONET_WRITER_MODEL`, `MONET_QA_MODEL`, and
`MONET_PUBLISHER_MODEL` — any `init_chat_model()` string works.

## Run

```bash
python -m examples.social_media_llm "AI in marketing"
```

The CLI walks the three-graph supervisor topology:

1. **entry** — triage classifies the request (simple, bounded, complex)
2. **planning** — builds a work brief and interrupts for human approval,
   looping on feedback up to 3 revisions
3. **execution** — wave-based parallel agent execution with QA
   reflection and HITL gates on quality failures

## Researcher paths

`monet.agents.researcher` selects its search provider at call time:

| Condition | Path |
|---|---|
| `EXA_API_KEY` set + `exa_py` importable | Exa semantic search + LLM synthesis (preferred) |
| `TAVILY_API_KEY` set + `langchain_community` importable | Tavily ReAct agent |
| neither | LLM-only synthesis with a warning |

Manual smoke run for each path:

```bash
export EXA_API_KEY=...   && python -m examples.social_media_llm "AI in marketing"
unset EXA_API_KEY && export TAVILY_API_KEY=... && python -m examples.social_media_llm "AI in marketing"
unset EXA_API_KEY && unset TAVILY_API_KEY && python -m examples.social_media_llm "AI in marketing"
```

The LLM-only path stores artifacts with confidence 0.6; the search-
backed paths use 0.85.

## What to look for

- **Streaming progress:** `cli.py` subscribes to
  `astream(stream_mode=["updates","custom"])`. The `custom` channel
  carries `emit_progress({...})` events from inside the agents.
- **HITL resume:** both planning approval and execution wave interrupts
  use `Command(resume=...)` with typed payloads. Planning expects
  `{"approved": bool, "feedback": str | None}`; execution expects
  `{"action": "continue"|"abort", "feedback": str | None}`.
- **Wave parallelism:** the execution graph fans out each wave via
  LangGraph's `Send` API. Watch the progress stream for interleaved
  agent events.
- **Catalogue artifacts:** every agent writes its output to
  `$MONET_CATALOGUE_DIR/artifacts/` via `get_catalogue().write(...)`.
  The CLI only keeps pointers in graph state.

## Observability

If you run a Langfuse collector (see `docker-compose.dev.yml`), every
graph invocation emits OpenTelemetry traces with agent names, wave
indices, and signal metadata. The reference agents use the
`emit_progress()` and `emit_signal()` SDK helpers so all activity is
visible from the streaming channel and the trace hierarchy.
