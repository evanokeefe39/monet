# social_media_llm — LangGraph Server client for the monet SDK

A minimal interactive terminal client that drives the monet SDK's
built-in reference agents and graphs via a **LangGraph Server**. This
is the production shape: graphs are compiled on the server at startup
(so build-time `_assert_registered(...)` runs once, loudly), and the
CLI is a thin `langgraph-sdk` client that creates threads, streams
runs, and resumes interrupts.

All agents and graphs used here live in the SDK:

- `monet.agents` — planner, researcher, writer, qa, publisher
- `monet.orchestration` — `build_entry_graph`, `build_planning_graph`,
  `build_execution_graph`, and the `EntryState` / `PlanningState` /
  `ExecutionState` TypedDicts

The example itself contains only client code:

| File | Role |
|---|---|
| `cli.py`            | Click entry point |
| `app.py`            | dotenv, catalogue config, env/server checks |
| `client.py`         | langgraph-sdk client wiring + streaming helper |
| `workflow.py`       | three phase functions + HITL resume loops |
| `display.py`        | print helpers + artifact-aware wave renderer |
| `prompts.py`        | HITL input helpers |
| `server_graphs.py`  | server-side shim: imports `monet.agents`, configures the catalogue, re-exports the three builders |
| `langgraph.json`    | registers the three graphs for `langgraph dev` |

## Install

The example is a standalone uv project with its own `.venv`. From the
repo root:

```bash
cd examples/social_media_llm
uv sync
```

That pulls in monet (as an editable path dependency on the parent repo)
plus the full reference stack — Gemini, Groq, langchain-community,
tavily-python, exa-py, python-dotenv — and the `langgraph-cli[inmem]`
+ `langgraph-sdk` + `click` packages that drive the two-process flow.

## Environment

Copy the template and fill in your keys — `python-dotenv` loads `.env`
on startup in both the CLI and server processes.

```bash
cp .env.example .env
```

Required keys:

```bash
GEMINI_API_KEY=...    # planner / researcher / writer / publisher
GROQ_API_KEY=...      # qa
TAVILY_API_KEY=...    # optional: Tavily ReAct path for researcher/deep
EXA_API_KEY=...       # optional: Exa semantic search path (preferred)
```

Model selection is controlled via `MONET_PLANNER_MODEL`,
`MONET_RESEARCHER_MODEL`, `MONET_WRITER_MODEL`, `MONET_QA_MODEL`, and
`MONET_PUBLISHER_MODEL` — any `init_chat_model()` string works.

`MONET_CATALOGUE_DIR` is **optional**. When unset, both the CLI and the
server default to `<example_dir>/.catalogue` (anchored to the location
of `app.py` / `server_graphs.py`), so both processes resolve to the
same on-disk directory regardless of which working directory they were
started from. If you set it explicitly, use an **absolute** path — a
relative value will resolve against each process's cwd and the two
sides will desync.

## Run — two processes

### Terminal A — start the LangGraph dev server

```bash
cd examples/social_media_llm
uv run langgraph dev
```

This reads `langgraph.json`, loads `server_graphs.py` (which imports
`monet.agents` to populate the agent registry, wires a filesystem
catalogue, and re-exports the three builders), and exposes an HTTP
API on <http://localhost:2024>. Leave it running.

### Terminal B — run the CLI

```bash
cd examples/social_media_llm
uv run python cli.py "AI in marketing"
```

Options:

- `--server-url` (env `MONET_LANGGRAPH_URL`) — default
  `http://localhost:2024`
- `--run-id` — override the generated 8-char run id

The CLI walks the three-graph supervisor topology:

1. **entry** — triage classifies the request (simple / bounded / complex)
2. **planning** — builds a work brief and interrupts for human approval,
   looping on feedback up to 5 revisions
3. **execution** — wave-based parallel agent execution with QA
   reflection and HITL gates on quality failures

If the server is not reachable the CLI exits non-zero with a friendly
error.

## What to look for

- **Streaming progress.** `client.stream_run` wraps
  `client.runs.stream(..., stream_mode=["updates","custom"])`. The
  `custom` channel carries `emit_progress({...})` events from inside
  the agents; the `updates` channel carries per-node state diffs.
- **HITL resume.** Planning and execution interrupts both use
  `client.runs.stream(..., command={"resume": payload})`. After each
  stream drains, the CLI fetches thread state via
  `client.threads.get_state()` and inspects `state["next"]` to decide
  whether to prompt again.
- **Wave parallelism.** The execution graph fans out each wave via
  LangGraph's `Send` API. Watch the progress stream for interleaved
  agent events.
- **Catalogue artifacts, resolved at the CLI.** Every agent writes its
  output to `<example_dir>/.catalogue/artifacts/` via
  `get_catalogue().write(...)`. The graph state only carries the
  `ArtifactPointer` — the CLI's `display.print_wave_results()` calls
  `catalogue.read(artifact_id)` directly to pull the bytes back for
  rendering. **No regex parsing of `wave_result.output`** — that was
  a pre-v3 hack and is gone.

## Researcher paths

`monet.agents.researcher` selects its search provider at call time:

| Condition | Path |
|---|---|
| `EXA_API_KEY` set + `exa_py` importable | Exa semantic search + LLM synthesis (preferred) |
| `TAVILY_API_KEY` set + `langchain_community` importable | Tavily ReAct agent |
| neither | LLM-only synthesis with a warning |

The LLM-only path stores artifacts with confidence 0.6; the
search-backed paths use 0.85.

## Observability

If you run a Langfuse collector (see `docker-compose.dev.yml` at the
repo root), every graph invocation emits OpenTelemetry traces with
agent names, wave indices, and signal metadata. The reference agents
use the `emit_progress()` and `emit_signal()` SDK helpers so all
activity is visible from the streaming channel and the trace hierarchy.
