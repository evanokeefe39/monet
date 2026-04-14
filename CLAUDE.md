# CLAUDE.md

## Project

monet is a multi-agent orchestration SDK for Python. MIT licensed, solo maintainer. The SDK provides an `@agent` decorator with pool assignment, typed context injection, an artifact store, orchestration via LangGraph with a task queue, and a FastAPI server layer. Keep things minimal and clean.

## Layout

- `src/monet/` — package source (src layout)
  - Top-level modules (stable public surface): `types.py` (`AgentResult`, `AgentRunContext`, `Signal`, `ArtifactPointer`), `signals.py` (`SignalType` vocabulary + group frozensets), `streams.py` (`AgentStream`), `handlers.py` (stream handler factories), `descriptors.py` (capability descriptors), `exceptions.py` (`SemanticError`, `EscalationRequired`, `NeedsHumanReview`), `tracing.py` (public tracing API), `agent_manifest.py` (`configure_agent_manifest`, `get_agent_manifest` — orchestration-side).
  - `_constants.py` — canonical local ports (`STANDARD_POSTGRES_PORT=5432`, `STANDARD_REDIS_PORT=6379`, `STANDARD_DEV_PORT=2026`, `STANDARD_LANGFUSE_PORT=3000`) and `state_file()` helper.
  - `config/` — central configuration subpackage. `_env.py` is the single boundary where the SDK touches `os.environ` (registers every `MONET_*` name as `Final[str]`, exposes typed accessors `read_str/bool/float/int/path/enum`, defines `ConfigError`). `_load.py` owns `monet.toml` reading (`default_config_path`, `read_toml`, `read_toml_section`). `_schema.py` defines per-unit pydantic schemas (`ServerConfig`, `WorkerConfig`, `ClientConfig`, `ObservabilityConfig`, `ArtifactsConfig`, `QueueConfig`, `AuthConfig`, `OrchestrationConfig`, `CLIDevConfig`), each with `load()`, `validate_for_boot()`, and `redacted_summary()` methods. Every deployable unit validates its config at boot and emits a redacted summary at `INFO` — malformed values fail fast, never silent. See `docs/reference/env-vars.md` for the full registry.
  - `_graph_config.py` — loads `monet.toml [graphs]` role mapping and `[entrypoints.<name>]` declarations via `monet.config._load.read_toml`. `DEFAULT_ENTRYPOINTS` + `load_entrypoints()` gate which graphs `MonetClient.run` and `monet run` can invoke. `Entrypoint` is a one-field `TypedDict({"graph": str})` — no `kind` field.
  - `core/` — worker-side internals: `decorator.py` (`@agent`), `registry.py` (handler registry), `manifest.py` (capability declarations), `context.py` (`contextvars` run context), `context_resolver.py` (`resolve_context` — artifact store read), `tracing.py` (OTel setup), `hooks.py` (`GraphHookRegistry`, `@on_hook`), `stubs.py` (`emit_progress` / `emit_signal` / `write_artifact`), `artifacts.py` (worker-side handle), `worker_client.py`, `_agents_config.py`, `_retry.py`, `_serialization.py`.
  - `cli/` — click commands: `_dev.py` (`monet dev` as a group with `down` subcommand + auto-teardown), `_run.py` (default-pipeline vs single-graph dispatch), `_runs.py`, `_chat.py`, `_worker.py`, `_register.py`, `_server.py`, `_status.py`, `_render.py`, `_discovery.py`, `_setup.py`.
  - `client/` — `MonetClient` graph-agnostic client (`__init__.py`), core events (`_events.py`: `RunStarted`, `NodeUpdate`, `AgentProgress`, `SignalEmitted`, `Interrupt`, `RunComplete`, `RunFailed`, plus `RunSummary`, `RunDetail`, `PendingDecision`, `ChatSummary`), boundary errors (`_errors.py`: `MonetClientError` + `RunNotInterrupted`, `AlreadyResolved`, `AmbiguousInterrupt`, `InterruptTagMismatch`, `GraphNotInvocable`), wire helpers (`_wire.py`: `task_input`, `chat_input`, `stream_run`, `get_state_values`, `create_thread`, metadata keys — documented adapter API), run-state cache (`_run_state.py`: `{run_id: {graph_id: thread_id}}`).
  - `pipelines/default/` — default multi-graph pipeline adapter (entry → planning → execution with HITL plan approval). `adapter.py` (`run(client, topic, auto_approve=...)` composes threads and projects core events), `events.py` (`TriageComplete`, `PlanReady`, `PlanApproved`, `PlanInterrupt`, `WaveComplete`, `ReflectionComplete`, `ExecutionInterrupt`, `DefaultInterruptTag` Literal, TypedDict payloads, `DefaultPipelineRunDetail` typed view), `_inputs.py` (`planning_input`, `execution_input`), `_hitl.py` (typed verbs: `approve_plan`, `revise_plan`, `reject_plan`, `retry_wave`, `abort_run` — each wraps `client.resume`), `render.py` (terminal rendering).
  - `hooks/` — built-in graph hooks: `plan_context.py` (`inject_plan_context` worker hook that resolves `work_brief_pointer` + `node_id` into task content).
  - `queue/` — `TaskQueue` protocol + `_worker.run_worker` + backends: `memory`, `redis`, `sqlite`, `upstash`.
  - `orchestration/` — flat-DAG graphs: `entry_graph.py`, `planning_graph.py`, `execution_graph.py`, `chat_graph.py`. State schemas and `RoutingSkeleton`/`RoutingNode` pydantic models in `_state.py`. `_invoke.py` (queue-only dispatch), `_signal_router.py`, `_validate.py`.
  - `server/` — `bootstrap()` with guaranteed ordering (`_bootstrap.py`), lazy-worker wiring, Aegra custom HTTP routes (`_aegra_routes.py`), langgraph config generation (`_langgraph_config.py`), `default_graphs.py` (exports the four built-in graphs).
  - `agents/` — reference agents using the partial form (planner, researcher, writer, qa, publisher).
  - `artifacts/` — artifact store: index, memory, metadata, storage, protocol, service, `artifacts_from_env()` helper.
- `tests/` — pytest test directory (393 tests at last count). `_fakes.py` centralizes the fake LangGraph SDK client used by client/adapter tests. `test_default_pipeline_events.py` pins the pointer-only state shape + wave batching + PlanInterrupt / RunFailed projections. `test_public_api.py` pins the public surface of `monet`, `monet.client`, `monet.pipelines.default`.
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
- **Pointer-only orchestration, flat DAG execution** (post-commit `a176030`): planner writes a full `work_brief` to the artifact store and emits a `work_brief_pointer` plus an inline `RoutingSkeleton` (`{goal, nodes}`). `RoutingNode` carries `{id, agent_id, command, depends_on}`. Execution traverses the DAG via `completed_node_ids` — phases and wave indices were removed. Agent-side `inject_plan_context` hook resolves the pointer at invocation time.
- Agent SDK (`@agent` decorator, `AgentStream`, signals, exceptions): `docs/guides/agents.md`, `docs/api/core.md`.
- **Hooks subsystem** (`core/hooks.py`, `hooks/`): two extension points. Worker hooks via `@on_hook("before_agent" | "after_agent")` run in the worker process and get the task/context/result envelope. Graph hooks via `GraphHookRegistry` run in the server process at declared points (e.g. `before_wave`, `after_wave_server`, custom names in user graphs). The `examples/custom-graph` example demonstrates both.
- Orchestration (invoke_agent, task queue, pointer-only state, signal routing): `docs/guides/orchestration.md`, `docs/api/orchestration.md`.
- Distribution (pools, workers, monet.toml, CLI): `docs/guides/distribution.md`, `docs/api/server.md`.
- Artifact store (artifact storage, metadata): `docs/guides/artifacts.md`, `docs/api/artifacts.md`.
- Observability (OTel, Langfuse, trace continuity): `docs/guides/observability.md`.
- Server: Aegra (Apache 2.0 LangGraph Platform replacement) for dev and production. `monet dev` shells to `aegra dev`, production uses `aegra serve`. Worker/task routes mounted as Aegra custom HTTP routes via `_aegra_routes.py`.
- **CLI surface**: `monet dev` (group: default=start, `monet dev down` for teardown), `monet run` (default pipeline vs single-graph via `--graph <entrypoint>`), `monet runs` (list/inspect/pending/resume), `monet chat`, `monet worker`, `monet register`, `monet server`, `monet status`.
- **Config-declared entrypoints**: `monet.toml [entrypoints.<name>]` with just `graph = "<id>"` declares which graphs `MonetClient.run` / `monet run --graph` can invoke. Default is `{"default": {"graph": "entry"}}`. Internal subgraphs (`planning`, `execution`) are intentionally un-invocable. Adding a new invocable graph is a config change, not a code change. The `kind` field was removed in the client-decoupling refactor — all invocable graphs are driven as single-graph streams via `MonetClient.run`; multi-graph compositions ship as adapter modules (see `monet.pipelines.default`).
- **Client / pipeline split**: `MonetClient` is graph-agnostic — `run(graph_id, input)` streams core events, `resume(run_id, tag, payload)` dispatches to a paused interrupt with validation (`RunNotInterrupted` / `AlreadyResolved` / `AmbiguousInterrupt` / `InterruptTagMismatch`), `abort(run_id)` terminates. Pipeline-specific composition (entry → planning → execution with HITL) lives in `monet.pipelines.default.adapter.run(client, topic, ...)` as an adapter that uses the client's wire primitives directly. HITL verbs (`approve_plan`, etc.) are thin wrappers over `client.resume` with typed `DefaultInterruptTag` (`Literal["human_approval", "human_interrupt"]`) and `TypedDict` payloads.

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
- End-to-end integration tests: the test suite covers unit and component tests, but has no E2E coverage across deployment topologies. Needs tests for: (1) `monet dev` → `monet run` full pipeline with HITL approve/revise/reject, (2) `aegra serve` with external Postgres, (3) multiple concurrent `monet worker` instances claiming from the same server, (4) `MONET_QUEUE_BACKEND=redis` and `sqlite` queue backends under load, (5) custom graph registration via `aegra.json` with non-monet graphs driven via `--graph`, (6) worker reconnection after server restart, (7) the `monet run --auto-approve` happy path end-to-end.

## Deferred from client-decoupling refactor

The refactor that removed `kind` from entrypoints and split `monet.pipelines.default` out of `MonetClient` intentionally left three items on the table. Each has a clear trigger for picking it back up.

- **Pluggable pipeline adapters via config**: `[entrypoints.<name>]` currently takes only `graph = "<id>"`. A future `adapter = "<module.path>"` field would let users register custom multi-graph compositions the way `monet.pipelines.default` is registered today. Trigger: the second pipeline adapter appears in-tree or in an example. Until then, the single adapter is reached by importing `monet.pipelines.default.adapter.run` directly.
- **In-process (no-server) programmatic driver**: `src/monet/_run.py` was deleted. It was the only path that ran the full pipeline in-process with a `MemorySaver` checkpointer — no server needed. Library callers now use `monet dev` + `MonetClient`, or shell to `aegra dev`. Trigger: a concrete need for server-less library usage (e.g. a notebook example, a CLI subcommand that wants to avoid Docker). If reintroduced, the driver should consume the default pipeline adapter rather than duplicating composition logic.
- **Graph ↔ client interrupt wire-contract test against real graphs**: `tests/test_default_pipeline_events.py` covers the adapter's projection of interrupts via fake SDK chunks, but there is no test that builds the real `planning_graph` / `execution_graph` with a `MemorySaver`, drives them to an `interrupt(...)` call, and asserts that the client-side `Interrupt(tag, values, next_nodes)` parse matches the graph's actual kwargs. Trigger: any change to the `human_approval` / `human_interrupt` nodes' `interrupt(...)` payload shape, or the first time LangGraph's `state.next` semantics bite us for a parallel-branch interrupt. The test file would live at `tests/test_interrupt_wire_contract.py`.

## Roadmap

Forward-looking commitments. See `docs/architecture/roadmap.md` for the full shipped/planned ledger. Items here are prioritized and will be picked up as standalone plans.

### Priority 1 — SaaS enabling primitives (no SaaS built here)

The SaaS platform itself — user management, accounts, billing, usage limits, customer UI — will live in a **separate downstream repo that imports monet**. This repo's only job is to expose the primitives that downstream repo needs. Out of scope here: anything that requires a user model or a billing model. This repo will never grow a user model, billing logic, or customer-facing productization.

Queue plane is already SaaS-compatible (all backends pull-only, no worker inbound). Control-plane extension points to add:

- **Pluggable auth dependency** in `src/monet/server/_auth.py`: swap the `MONET_API_KEY` singleton for a FastAPI dependency the downstream repo can replace. Default stays single-key for self-hosted.
- **Tenant ID as request-context primitive**: `TenantContext` propagated via `Depends`, opaque string, monet does not model what a tenant is.
- **Tenant-scoped queries**: runs, threads, artifacts, pending decisions filter by `tenant_id` when present; unscoped when absent — `src/monet/server/_routes.py`, `src/monet/client/_wire.py`, `src/monet/artifacts/_service.py`.
- **Credential passthrough on clients**: `MonetClient(url, api_key=...)` and `WorkerClient(api_key=...)` carry an opaque bearer; server decides how to validate.
- **Server-side pool-claim validation** against tenant context — prevents cross-tenant task stealing on shared Redis/Upstash.

### Priority 2 — Push pool dispatch (enables Cloud Run / Lambda / Vercel workers)

Config schema already declares `type = "push"` in `[pools.<name>]` but `src/monet/orchestration/_invoke.py` silently enqueues for all pool types. Need:

- Forwarding worker that claims push-pool tasks and POSTs to the pool's configured URL with auth.
- Lease TTL + sweeper so crashed push tasks requeue.
- Removes push pool from `## Unimplemented` once shipped.

### Lower priority / triggered

- **Pluggable pipeline adapters via `monet.toml`** — trigger: second adapter in-tree (see `## Deferred from client-decoupling refactor`).
- **In-process driver reintroduction (`_run.py`)** — trigger: concrete need for library-only usage (e.g. notebook example). See `## Deferred`. `src/monet/__main__.py` was deleted with `_run.py`; if a driver returns, `python -m monet` can be routed through it.
- **Graph ↔ client interrupt wire-contract test** — trigger: any change to `human_approval` / `human_interrupt` interrupt kwargs. See `## Deferred`.
- **E2E integration tests** across deployment topologies — see `## Unimplemented`.
- **Optional summarizer agent** — framework-inserted wave context condensation; see `docs/architecture/roadmap.md`.

## Refactor history

- **Client decoupling** (current HEAD): `_events.py` split into graph-agnostic core + `monet.pipelines.default.events`. `MonetClient.run(graph_id, input)` replaces the pipeline-composition `run(topic)`. HITL methods removed from `MonetClient`; typed verbs live in `monet.pipelines.default._hitl` as wrappers over `client.resume(run_id, tag, payload)`. `_run.py` deleted. `Entrypoint.kind` removed. See `docs/guides/client.md` and `docs/api/client.md`.