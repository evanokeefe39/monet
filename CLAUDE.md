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

System design is outlined in SPEC.md and TARGET_ARCHITECTURE.md. Key design principles: agents are opaque capability units with a uniform interface, the orchestrator owns routing and HITL policy, OpenTelemetry observability is non-negotiable, and context engineering (controlling exactly what the model sees) is prioritized over prompt gymnastics.

Orchestration/execution separation:
- `invoke_agent()` dispatches via TaskQueue (enqueue + poll_result). Transport is a worker concern.
- Workers claim tasks by pool (Prefect model). Three pool types planned: local (sidecar), pull (remote), push (cloud forwarding).
- `AgentManifest` (orchestration-side) tracks what agents are available and their pool assignment. `AgentRegistry` (worker-side) maps to executable handlers.
- `_assert_registered` checks the manifest at graph build time. `invoke_agent` checks manifest before enqueue, returns `CAPABILITY_UNAVAILABLE` signal instantly if missing.
- Pointer-only state: `_resolve_wave_result` returns summaries + catalogue pointers. Full content stays in catalogue. Agents call `resolve_context()` when they need upstream content.

Decorator/stream/orchestrator division of labour:
- The `@agent` decorator is registration + context injection only. It populates both the handler registry and the capability manifest (with pool assignment).
- `AgentStream` is the translation boundary between an external agent's output and the SDK primitives.
- The orchestrator reads `AgentResult.output` (inline string/dict/None) and `AgentResult.artifacts` (catalogue pointers) as distinct fields.
- Large string returns from `@agent` functions are auto-offloaded to the catalogue when content exceeds `DEFAULT_CONTENT_LIMIT`. The pointer lands in `artifacts`; `output` becomes a 200-char inline summary.

Three-graph supervisor topology:
- Entry graph: triage via planner/fast → simple or complex routing
- Planning graph: planner/plan → human approval gate → work brief output
- Execution graph: wave-based parallel execution via Send, QA reflection gates, retry budget, signal routing (BLOCKING → HITL interrupt, RECOVERABLE → retry)
