# CLAUDE.md

## Project

monet is a multi-agent orchestration SDK for Python. MIT licensed, solo maintainer. The SDK provides an `@agent` decorator with pool assignment, typed context injection, a catalogue/artifact system, orchestration via LangGraph with a task queue, and a FastAPI server layer. Keep things minimal and clean.

## Layout

- `src/monet/` — package source (src layout)
  - `_decorator.py` — `@agent` decorator (core public API). Dual call form: `agent("id")` returns a partial, `@agent(agent_id=..., command=..., pool=...)` is the verbose form. Populates both handler registry and capability manifest.
  - `types.py` — `AgentResult` (output: `str | dict | None`), `AgentRunContext`, `Signal`, `ArtifactPointer`
  - `signals.py` — `SignalType` vocabulary + `BLOCKING`/`RECOVERABLE`/`INFORMATIONAL`/`AUDIT`/`ROUTING` frozensets. Includes `CAPABILITY_UNAVAILABLE` for missing agents. Orchestrator routes on group membership, never raw strings.
  - `streams.py` — `AgentStream`: typed async event bus for external agents. `.cli()`, `.sse()`, `.http()` constructors, `.on()` handler builder, `.run()` execution.
  - `handlers.py` — handler factories for `AgentStream.on()`: `webhook_handler` (with timeout + error handling), `log_handler`
  - `_context.py` — `contextvars`-based run context (`get_run_context`, `get_run_logger`)
  - `_context_resolver.py` — `resolve_context()`: fetches full catalogue content from pointer-only context entries. Called by agents, never by orchestration.
  - `_registry.py` — agent/command handler registration (worker-side only)
  - `_manifest.py` — `AgentManifest`: capability declarations with pool assignment (orchestration-side). Decoupled from handler registry.
  - `queue.py` — `TaskQueue` protocol: enqueue, poll_result, claim (by pool), complete, fail, cancel
  - `_queue_memory.py` — `InMemoryTaskQueue`: per-pool asyncio queues, O(1) claim, backpressure, memory cleanup
  - `_queue_worker.py` — `run_worker()`: concurrent task execution (semaphore-capped), OTel spans, graceful shutdown, pool-based claiming (Prefect model)
  - `server.py` — `bootstrap()`: one-call server init (tracing → catalogue → manifest → queue → worker). Worker health monitoring.
  - `_stubs.py` — ambient trio: `emit_progress`, `emit_signal`, `write_artifact`
  - `_catalogue.py` — `get_catalogue()` handle; `write()` appends pointers to the decorator's artifact collector
  - `_tracing.py` — OpenTelemetry integration
  - `descriptors.py` — capability descriptors
  - `exceptions.py` — `SemanticError`, `EscalationRequired`, `NeedsHumanReview`
  - `agents/` — reference agents (planner, researcher, writer, qa, publisher) using the partial form
  - `catalogue/` — artifact catalogue: index, memory, metadata, storage, protocol, service, `catalogue_from_env()` helper
  - `orchestration/` — LangGraph orchestration: state schemas (EntryState, PlanningState, ExecutionState), node wrapper, invoke (queue-only dispatch), three graph builders (`entry`, `planning`, `execution`), `_validate._assert_registered` checks manifest
- `tests/` — pytest test directory
- `docs/` — mkdocs-material documentation

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

- Three-graph supervisor topology (entry, planning, execution): `docs/architecture/graph-topology.md`
- Agent SDK (`@agent` decorator, `AgentStream`, signals, exceptions): `docs/guides/agents.md`, `docs/api/core.md`
- Orchestration (invoke_agent, task queue, pointer-only state, signal routing): `docs/guides/orchestration.md`, `docs/api/orchestration.md`
- Distribution (pools, workers, monet.toml, CLI): `docs/guides/distribution.md`, `docs/api/server.md`
- Catalogue (artifact storage, metadata): `docs/guides/catalogue.md`, `docs/api/catalogue.md`
- Observability (OTel, Langfuse, trace continuity): `docs/guides/observability.md`
- Server: Aegra (Apache 2.0 LangGraph Platform replacement) for dev and production. `monet dev` shells to `aegra dev`, production uses `aegra serve`. Worker/task routes mounted as Aegra custom HTTP routes via `_aegra_routes.py`.

## Aegra compatibility constraints

Aegra's graph loader (`langgraph_service.py`) only supports filesystem paths in `aegra.json` graphs, not Python module paths. It also splits on `:` to separate file path from export name, which breaks absolute Windows paths (`C:\...`). `_langgraph_config.py:write_config()` resolves module paths to relative file paths before writing.

Aegra's factory classifier inspects graph builder signatures: a 1-arg function whose parameter isn't `ServerRuntime` is treated as a config-accepting factory and called with a `RunnableConfig` dict. The real graph builders (`build_entry_graph`, etc.) accept an optional `hooks: GraphHookRegistry | None` kwarg, so `default_graphs.py` wraps them as 0-arg functions to prevent misclassification. Any new graph builder exported via `default_graphs.py` must also be wrapped as 0-arg.

## Unimplemented

- Push pool dispatch: `_config.py` declares a `push` pool type (for Cloud Run, Vercel Functions, Lambda) with URL + auth config, but no dispatch implementation exists. `invoke_agent` always enqueues to the task queue for pull-based workers. Implementing push requires a dispatcher in the orchestration layer that POSTs tasks to the pool's configured URL instead of enqueuing.
- End-to-end integration tests: the test suite covers unit and component tests but has no E2E coverage across deployment topologies. Needs tests for: (1) `monet dev` → `monet run` full pipeline with HITL approve/revise/reject, (2) `aegra serve` with external Postgres, (3) multiple concurrent `monet worker` instances claiming from the same server, (4) `MONET_QUEUE_BACKEND=redis` and `sqlite` queue backends under load, (5) custom graph registration via `aegra.json` with non-monet graphs, (6) worker reconnection after server restart, (7) the `monet run --auto-approve` happy path end-to-end. Current tests mock the queue and graph layers — no test starts a real server and drives a real run through it.
- More complex planning graph e.g. tangential investigations with research agent (similar to claude ultraplan)
- Other workflows than plan and execute. Perhaps an iterate forever until approved result, multi stage review gates and multiple reviewers (config extensible lifecycle states)
- 