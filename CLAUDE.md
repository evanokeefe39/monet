# CLAUDE.md

## Project

monet is a multi-agent orchestration SDK for Python. MIT licensed, solo maintainer. The SDK provides an `@agent` decorator with pool assignment, typed context injection, a catalogue/artifact system, orchestration via LangGraph with a task queue, and a FastAPI server layer. Keep things minimal and clean.

## Layout

- `src/monet/` — package source (src layout)
  - Top-level modules (stable public surface): `types.py` (`AgentResult`, `AgentRunContext`, `Signal`, `ArtifactPointer`), `signals.py` (`SignalType` vocabulary + group frozensets), `streams.py` (`AgentStream`), `handlers.py` (stream handler factories), `descriptors.py` (capability descriptors), `exceptions.py` (`SemanticError`, `EscalationRequired`, `NeedsHumanReview`), `tracing.py` (public tracing API), `agent_manifest.py` (`configure_agent_manifest`, `get_agent_manifest` — orchestration-side).
  - `_constants.py` — canonical local ports (`STANDARD_POSTGRES_PORT=5432`, `STANDARD_REDIS_PORT=6379`, `STANDARD_DEV_PORT=2026`, `STANDARD_LANGFUSE_PORT=3000`) and `state_file()` helper.
  - `_graph_config.py` — loads `monet.toml [graphs]` role mapping and `[entrypoints.<name>]` declarations. `DEFAULT_ENTRYPOINTS` + `load_entrypoints()` gate which graphs `monet run` can invoke.
  - `_run.py` — programmatic `run()` async iterator (used by library callers; CLI uses `MonetClient` instead).
  - `core/` — worker-side internals: `decorator.py` (`@agent`), `registry.py` (handler registry), `manifest.py` (capability declarations), `context.py` (`contextvars` run context), `context_resolver.py` (`resolve_context` — catalogue read), `tracing.py` (OTel setup), `hooks.py` (`GraphHookRegistry`, `@on_hook`), `stubs.py` (`emit_progress` / `emit_signal` / `write_artifact`), `catalogue.py` (worker-side handle), `worker_client.py`, `_agents_config.py`, `_retry.py`, `_serialization.py`.
  - `cli/` — click commands: `_dev.py` (`monet dev` as a group with `down` subcommand + auto-teardown), `_run.py` (entrypoint-aware dispatch: pipeline / single / messages), `_runs.py`, `_chat.py`, `_worker.py`, `_register.py`, `_server.py`, `_status.py`, `_render.py`, `_discovery.py`, `_setup.py`.
  - `client/` — `MonetClient` high-level client (`__init__.py`), typed events (`_events.py`), wire helpers (`_wire.py`), run-state cache (`_run_state.py`).
  - `hooks/` — built-in graph hooks: `plan_context.py` (`inject_plan_context` worker hook that resolves `work_brief_pointer` + `node_id` into task content).
  - `queue/` — `TaskQueue` protocol + `_worker.run_worker` + backends: `memory`, `redis`, `sqlite`, `upstash`.
  - `orchestration/` — flat-DAG graphs: `entry_graph.py`, `planning_graph.py`, `execution_graph.py`, `chat_graph.py`. State schemas and `RoutingSkeleton`/`RoutingNode` pydantic models in `_state.py`. `_invoke.py` (queue-only dispatch), `_signal_router.py`, `_validate.py`.
  - `server/` — `bootstrap()` with guaranteed ordering (`_bootstrap.py`), lazy-worker wiring, Aegra custom HTTP routes (`_aegra_routes.py`), langgraph config generation (`_langgraph_config.py`), `default_graphs.py` (exports the four built-in graphs).
  - `agents/` — reference agents using the partial form (planner, researcher, writer, qa, publisher).
  - `catalogue/` — artifact catalogue: index, memory, metadata, storage, protocol, service, `catalogue_from_env()` helper.
- `tests/` — pytest test directory (393 tests at last count; client pointer-shape tests in `test_client_pointer_shape.py` pin the post-`a176030` state schema).
- `docs/` — mkdocs-material documentation.

## Commands

```bash
uv sync --group dev    # install all dependencies
uv run pytest          # run tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy src/       # type check
uv run mkdocs serve    # local docs preview
```

## Dependencies

Runtime: pydantic, opentelemetry-api/sdk, sqlalchemy, fastapi, uvicorn, langgraph.
Dev: pytest, pytest-asyncio, hypothesis, httpx, ruff, mypy, mkdocs-material, pre-commit.

## Code standards

- Python 3.12+, type annotations on all public API
- `py.typed` marker is present — maintain inline types
- mypy strict mode, zero errors required
- ruff for linting and formatting, line length 88
- Pre-commit hooks enforce ruff and mypy before each commit

## Testing

- All tests in `tests/` using pytest, async mode auto
- Test files named `test_*.py`
- Every public function needs a corresponding test
- conftest.py provides autouse `_queue_worker` fixture: creates InMemoryTaskQueue + background worker for all async tests. invoke_agent works transparently.

## Style

- src layout, all imports from `monet`
- No unnecessary abstractions or speculative code
- Keep dependencies minimal — justify any new addition
- Docstrings on public API only, Google style

## CI

GitHub Actions runs ruff, mypy, and pytest on push/PR to main. All checks must pass.

## Do not

- Add dependencies without explicit approval
- Modify CI workflow without explicit approval
- Create files outside the established layout
- Add compatibility shims or backwards-compat code

## Architecture

Key design principles: agents are opaque capability units with a uniform interface, the orchestrator owns routing and HITL policy, OpenTelemetry observability is non-negotiable, and context engineering is prioritized over prompt gymnastics. See `docs/architecture/design-principles.md`.

- Three-graph supervisor topology plus chat: `entry`, `planning`, `execution`, `chat`. See `docs/architecture/graph-topology.md`.
- **Pointer-only orchestration, flat DAG execution** (post-commit `a176030`): planner writes a full `work_brief` to the catalogue and emits a `work_brief_pointer` plus an inline `RoutingSkeleton` (`{goal, nodes}`). `RoutingNode` carries `{id, agent_id, command, depends_on}`. Execution traverses the DAG via `completed_node_ids` — phases and wave indices were removed. Agent-side `inject_plan_context` hook resolves the pointer at invocation time.
- Agent SDK (`@agent` decorator, `AgentStream`, signals, exceptions): `docs/guides/agents.md`, `docs/api/core.md`.
- **Hooks subsystem** (`core/hooks.py`, `hooks/`): two extension points. Worker hooks via `@on_hook("before_agent" | "after_agent")` run in the worker process and get the task/context/result envelope. Graph hooks via `GraphHookRegistry` run in the server process at declared points (e.g. `before_wave`, `after_wave_server`, custom names in user graphs). The `examples/custom-graph` example demonstrates both.
- Orchestration (invoke_agent, task queue, pointer-only state, signal routing): `docs/guides/orchestration.md`, `docs/api/orchestration.md`.
- Distribution (pools, workers, monet.toml, CLI): `docs/guides/distribution.md`, `docs/api/server.md`.
- Catalogue (artifact storage, metadata): `docs/guides/catalogue.md`, `docs/api/catalogue.md`.
- Observability (OTel, Langfuse, trace continuity): `docs/guides/observability.md`.
- Server: Aegra (Apache 2.0 LangGraph Platform replacement) for dev and production. `monet dev` shells to `aegra dev`, production uses `aegra serve`. Worker/task routes mounted as Aegra custom HTTP routes via `_aegra_routes.py`.
- **CLI surface**: `monet dev` (group: default=start, `monet dev down` for teardown), `monet run` (entrypoint-aware), `monet runs` (list/inspect/pending/resume), `monet chat`, `monet worker`, `monet register`, `monet server`, `monet status`.
- **Config-declared entrypoints**: `monet.toml [entrypoints.<name>]` with `graph = "<id>"` + `kind = "pipeline" | "single" | "messages"` declares which graphs `monet run` can invoke. Default is `default = {graph="entry", kind="pipeline"}`. Internal subgraphs (`planning`, `execution`) are intentionally un-invocable; adding a new invocable custom graph is a config change, not a code change.

## Standard ports and example lifecycle

Every example uses the same canonical local ports (defined in `src/monet/_constants.py`):

- Postgres: `5432`
- Redis: `6379`
- Dev server (monet dev / Aegra): `2026`
- Langfuse (optional tracing stack): `3000`

**Only one example runs at a time.** `monet dev` records the active example's compose path in `~/.monet/state.json` and auto-tears-down the previous example's containers (parsed from `container_name:` lines in its `.monet/docker-compose.yml`) before starting. Volumes are preserved — re-entering an example keeps its Postgres data. `monet dev down` is the explicit teardown command.

## Aegra compatibility constraints

Aegra's graph loader (`langgraph_service.py`) only supports filesystem paths in `aegra.json` graphs, not Python module paths. It also splits on `:` to separate file path from export name, which breaks absolute Windows paths (`C:\...`). `_langgraph_config.py:write_config()` resolves module paths to relative file paths before writing.

Aegra's factory classifier inspects graph builder signatures: a 1-arg function whose parameter isn't `ServerRuntime` is treated as a config-accepting factory and called with a `RunnableConfig` dict. The real graph builders (`build_entry_graph`, etc.) accept an optional `hooks: GraphHookRegistry | None` kwarg, so `default_graphs.py` wraps them as 0-arg functions to prevent misclassification. Any new graph builder exported via `default_graphs.py` must also be wrapped as 0-arg.

Per-example `.monet/docker-compose.yml` files are pre-baked in the example directory (not generated). Aegra's own `is_postgres_running` check (`aegra_cli/utils/docker.py`) treats any container on port 5432 as "ours" — without teardown it would pick up the previous example's Postgres and fail auth. The Phase 2 teardown in `src/monet/cli/_dev.py:_teardown_previous` prevents that; future compose files should still declare a `container_name:` so teardown can match them.

## Unimplemented

- Push pool dispatch: `_config.py` declares a `push` pool type (for Cloud Run, Vercel Functions, Lambda) with URL + auth config, but no dispatch implementation exists. `invoke_agent` always enqueues to the task queue for pull-based workers. Implementing push requires a dispatcher in the orchestration layer that POSTs tasks to the pool's configured URL instead of enqueuing.
- End-to-end integration tests: the test suite covers unit and component tests (incl. client pointer-shape tests), but has no E2E coverage across deployment topologies. Needs tests for: (1) `monet dev` → `monet run` full pipeline with HITL approve/revise/reject, (2) `aegra serve` with external Postgres, (3) multiple concurrent `monet worker` instances claiming from the same server, (4) `MONET_QUEUE_BACKEND=redis` and `sqlite` queue backends under load, (5) custom graph registration via `aegra.json` with non-monet graphs driven by the `single` entrypoint kind, (6) worker reconnection after server restart, (7) the `monet run --auto-approve` happy path end-to-end.
- Iterate-forever workflows (multi-stage review gates, multiple reviewers, config-extensible lifecycle states) — the `messages` entrypoint `kind` is reserved but not yet wired through `monet run`.

## Review next cycle — graph-coupling cleanup (open/closed violations)

Several code paths still hardcode the three built-in pipeline graph IDs (`entry`, `planning`, `execution`) instead of driving off the `[entrypoints]` config or a more general abstraction. Adding a new pipeline-shaped workflow would require touching all of these. Items to revisit:

- `src/monet/_run.py` — the programmatic `run()` generator imports `build_entry_graph` / `build_planning_graph` / `build_execution_graph` directly and hardcodes the triage → plan-approval → flat-DAG execution sequence. Should move to an entrypoint-driven dispatch mirror of the CLI, or be replaced by exposing `MonetClient` as the single library-facing entry.
- `src/monet/client/__init__.py` — `MonetClient.run()`, `approve_plan()`, `revise_plan()`, `retry_wave()`, `abort_run()`, `get_run()`, and `list_runs()` reference the literal `"entry" / "planning" / "execution"` graph keys and thread-tag values. That's fine today (the default pipeline always has those three) but locks custom pipeline topologies out. Extract a `Pipeline` abstraction keyed by entrypoint name.
- `src/monet/cli/_runs.py` — `render_run_detail` branches on `detail.phase == "planning"` / `"execution"` to pick what to show. A new workflow with different phase names would render blank.
- `src/monet/cli/_chat.py` — hardcodes `graph_ids["chat"]` for the `monet chat` REPL. Acceptable for now since `chat` has its own CLI command, but should align with the entrypoint `messages` kind when that's wired up.

Goal for next cycle: the only place a graph ID appears literally should be `src/monet/_graph_config.py` defaults; everything else reads from config. Anything that needs the pipeline shape should consume a `[entrypoints.<name>]` declaration via `load_entrypoints()`.