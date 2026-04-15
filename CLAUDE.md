# CLAUDE.md

## Project

monet is a multi-agent orchestration SDK for Python. MIT licensed, solo maintainer. The SDK provides an `@agent` decorator with pool assignment, typed context injection, an artifact store, orchestration via LangGraph with a task queue, and a FastAPI server layer. Keep things minimal and clean.

Known issues (bugs, deprecations, standards violations, design gaps) live in `ISSUES.md`. Roadmap features live under `## Roadmap` below. Check `ISSUES.md` before picking maintenance work — do not duplicate or paper over listed issues without explicit scope.

## Commits
use /caveman:commits for git commits

## Layout

- `src/monet/` — package source (src layout)
  - Top-level modules (stable public surface): `types.py` (`AgentResult`, `AgentRunContext`, `Signal`, `ArtifactPointer`, `build_artifact_pointer`, `find_artifact`), `signals.py` (`SignalType` vocabulary + group frozensets), `streams.py` (`AgentStream`), `handlers.py` (stream handler factories), `exceptions.py` (`SemanticError`, `EscalationRequired`, `NeedsHumanReview`), `tracing.py` (public tracing API), `agent_manifest.py` (`configure_agent_manifest`, `get_agent_manifest` — orchestration-side).
  - `_ports.py` — canonical local ports (`STANDARD_POSTGRES_PORT=5432`, `STANDARD_REDIS_PORT=6379`, `STANDARD_DEV_PORT=2026`, `STANDARD_LANGFUSE_PORT=3000`) and `state_file()` helper.
  - `config/` — central configuration subpackage. `_env.py` is the single boundary where the SDK touches `os.environ` (registers every `MONET_*` name as `Final[str]`, exposes typed accessors `read_str/bool/float/int/path/enum`, defines `ConfigError`). `_load.py` owns `monet.toml` reading (`default_config_path`, `read_toml`, `read_toml_section`). `_schema.py` defines per-unit pydantic schemas (`ServerConfig`, `WorkerConfig`, `ClientConfig`, `ObservabilityConfig`, `ArtifactsConfig`, `QueueConfig`, `AuthConfig`, `OrchestrationConfig`, `CLIDevConfig`), each with `load()`, `validate_for_boot()`, and `redacted_summary()` methods. `_graphs.py` loads `monet.toml [graphs]` role mapping and `[entrypoints.<name>]` declarations — `DEFAULT_ENTRYPOINTS` + `load_entrypoints()` gate which graphs `MonetClient.run` and `monet run` can invoke; `Entrypoint` is a one-field `TypedDict({"graph": str})`. Every deployable unit validates its config at boot and emits a redacted summary at `INFO` — malformed values fail fast, never silent. See `docs/reference/env-vars.md` for the full registry.
  - `core/` — worker-side internals: `decorator.py` (`@agent`), `registry.py` (handler registry), `manifest.py` (capability declarations), `context.py` (`contextvars` run context), `context_resolver.py` (`resolve_context` — artifact store read), `tracing.py` (OTel setup), `hooks.py` (`GraphHookRegistry`, `@on_hook`), `stubs.py` (`emit_progress` / `emit_signal` / `write_artifact`), `artifacts.py` (worker-side handle), `worker_client.py`, `_agents_config.py`, `_retry.py`, `_serialization.py`.
  - `cli/` — click commands: `_dev.py` (`monet dev` as a group with `down` subcommand + auto-teardown), `_run.py` (default-pipeline vs single-graph dispatch), `_runs.py`, `_chat.py`, `_worker.py`, `_register.py`, `_server.py`, `_status.py`, `_render.py`, `_discovery.py`, `_setup.py`, `_db.py` (`monet db migrate | current | stamp | check` for artifact-index alembic management).
  - `client/` — `MonetClient` graph-agnostic client (`__init__.py`), core events (`_events.py`: `RunStarted`, `NodeUpdate`, `AgentProgress`, `SignalEmitted`, `Interrupt`, `RunComplete`, `RunFailed`, plus `RunSummary`, `RunDetail`, `PendingDecision`, `ChatSummary`), boundary errors (`_errors.py`: `MonetClientError` + `RunNotInterrupted`, `AlreadyResolved`, `AmbiguousInterrupt`, `InterruptTagMismatch`, `GraphNotInvocable`), wire helpers (`_wire.py`: `task_input`, `chat_input`, `stream_run`, `get_state_values`, `create_thread`, metadata keys — documented adapter API), run-state cache (`_run_state.py`: `{run_id: {graph_id: thread_id}}`).
  - `pipelines/default/` — default multi-graph pipeline adapter (entry → planning → execution with HITL plan approval). `adapter.py` (`run(client, topic, auto_approve=...)` composes threads and projects core events), `events.py` (`TriageComplete`, `PlanReady`, `PlanApproved`, `PlanInterrupt`, `WaveComplete`, `ReflectionComplete`, `ExecutionInterrupt`, `DefaultInterruptTag` Literal, TypedDict payloads, `DefaultPipelineRunDetail` typed view), `_inputs.py` (`planning_input`, `execution_input`), `_hitl.py` (typed verbs: `approve_plan`, `revise_plan`, `reject_plan`, `retry_wave`, `abort_run` — each wraps `client.resume`), `render.py` (terminal rendering).
  - `hooks/` — built-in graph hooks: `plan_context.py` (`inject_plan_context` worker hook that resolves `work_brief_pointer` + `node_id` into task content).
  - `queue/` — `TaskQueue` protocol + `_worker.run_worker` + backends: `memory`, `redis`, `sqlite`, `upstash`.
  - `orchestration/` — flat-DAG graphs: `entry_graph.py`, `planning_graph.py`, `execution_graph.py`, `chat_graph.py`. State schemas and `RoutingSkeleton`/`RoutingNode` pydantic models in `_state.py`. `_invoke.py` (queue-only dispatch), `_signal_router.py`, `_validate.py`.
  - `server/` — `bootstrap()` with guaranteed ordering (`_bootstrap.py`), lazy-worker wiring, Aegra custom HTTP routes (`_aegra_routes.py`), langgraph config generation (`_langgraph_config.py`), `default_graphs.py` (exports the four built-in graphs).
  - `agents/` — reference agents using the partial form (planner, researcher, writer, qa, publisher).
  - `artifacts/` — artifact store: index, memory, metadata, storage, protocol, service, `artifacts_from_env()` helper. `_migrations.py` exposes programmatic alembic entry points (`apply_migrations`, `check_at_head`, `head_revision`, `stamp_head`) consumed by both `SQLiteIndex.initialise` (fail-fast in direct-construction paths) and the `monet db` CLI. `artifacts_from_env()` auto-migrates the default index for developer ergonomics; production deploys gate with `monet db check` and construct `SQLiteIndex` directly for fail-fast boot.
  - `_migrations/` — package-shipped alembic tree (`env.py`, `script.py.mako`, `versions/`). Sync-only env (no asyncio loops) so migrations can run from any context. Baseline revision `0001_baseline` creates the `artifacts` table; future revisions land via the standard alembic workflow.
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

## Code navigation

SymDex MCP is installed and the repo is registered (`~/.symdex/monet.db`). Prefer symdex tools over Read/Grep/Glob when locating, tracing, or understanding existing code:

- `mcp__symdex__search_symbols` / `get_symbol` — find functions, classes, methods by name with exact byte offsets
- `mcp__symdex__semantic_search` — find code by intent rather than exact name
- `mcp__symdex__get_callers` / `get_callees` — trace call graph
- `mcp__symdex__get_file_outline` / `get_repo_outline` — structure without reading full files
- `mcp__symdex__search_routes` — list HTTP endpoints across the codebase
- `mcp__symdex__get_index_status` — check freshness at session start; reindex via `index_repo` if stale (watcher is off by default)

Full-file `Read` is still correct for non-code files (toml/md/json) or when complete file context is required. The `symdex-code-search` skill documents the full tool surface.

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
- Add catch-all environment variables (e.g. `ENV=production`, `MODE=dev`, `STAGE=staging`) that toggle multiple unrelated behaviors. Each behavior gets its own explicit, named config knob — boot validation rejects on missing required values rather than branching on a single mode flag.

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

## Deployment scenarios

Six shapes. Full descriptions, wiring, and matrix in `docs/architecture/deployment-scenarios.md`. Short form:

- **S1 local all-in-one** — `monet dev` on a laptop, Docker-backed Postgres/Redis, `pool="local"` runs in-server. Tutorials and examples.
- **S2 self-hosted production** — `aegra serve` + managed Postgres/Redis on user infra, `monet worker --server-url ...` processes, shared `MONET_API_KEY`. Single tenant. `examples/deployed/server/` + `examples/deployed/worker/`.
- **S3 split fleet** — S2 with N worker pools across regions/hardware via `monet.toml [pools]`. Pull pools today; push pools ship via webhook to `pool.url` (see queue Phase 4). `examples/split-fleet/` ships both compose and Railway variants.
- **S4 workers-only** — `monet worker` with no server URL, `InMemoryTaskQueue`. Test/library only; no pipeline composition.
- **S5 SaaS** — vendor-hosted orchestrator, customer-hosted workers. Queue plane already compatible; control-plane primitives (pluggable auth, tenant ID, credential passthrough) pending. Productization (accounts, billing, UI) lives in a separate downstream repo that imports `monet`. See `## Roadmap` Priority 1.
- **S6 embedded / no-server** — removed with `_run.py` and `__main__.py`. Trigger to reintroduce: library-only use case. See `## Deferred from client-decoupling refactor`.

## Standard ports and example lifecycle

Every example uses the same canonical local ports (defined in `src/monet/_ports.py`):

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

- End-to-end integration tests: the test suite covers unit and component tests, but has no E2E coverage across deployment topologies. Needs tests for: (1) `monet dev` → `monet run` full pipeline with HITL approve/revise/reject, (2) `aegra serve` with external Postgres, (3) multiple concurrent `monet worker` instances claiming from the same server, (4) `RedisStreamsTaskQueue` under load against a real Redis, (5) custom graph registration via `aegra.json` with non-monet graphs driven via `--graph`, (6) worker reconnection after server restart, (7) the `monet run --auto-approve` happy path end-to-end, (8) push pool round trip with a live Cloud Run Service / Lambda Function URL.

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
- **Tenant-scoped stream keys** (`work:{tenant}:{pool}`) — trigger: Priority 1 lands. The current `work:{pool}` shape maps cleanly — one segment insertion, no protocol change.
- **Per-tenant rate limits on `/progress` and `/complete`** — trigger: Priority 1 lands and brings tenant context to request handling.

### Priority 2 — Push pool dispatch (shipped) + follow-ons

**Shipped in queue Phase 4** (commit log): `src/monet/orchestration/_invoke.py` now branches on `PoolConfig.type == "push"` and POSTs `{task_id, token, callback_url, payload}` directly to the pool's webhook URL via `httpx`. Workers run `monet worker --push` to stand up a FastAPI `POST /dispatch` endpoint (Cloud Run Service / Lambda Function URL / Azure Container Apps). Auth is HMAC-derived per task (`HMAC_SHA256(MONET_API_KEY, task_id)`) — no new signing-key env var. Batch providers (Cloud Run Jobs, ECS Fargate Task) use the public `monet.core.push_handler.handle_dispatch(...)` helper in a ~10-line user entry script. `handle_dispatch` is shared between the shipped FastAPI app and user scripts so there is no code duplication.

Follow-ons to pick up when a concrete user surfaces them:

- **Retry / circuit breaker on provider API failures** — today a webhook POST 5xx raises `RuntimeError`; there is no backoff + retry. Trigger: first observed transient throttling event on a real Cloud Run / Lambda integration.
- **Convenience provider extras** `monet[gcp]` / `monet[aws]` / `monet[azure]` / `monet[all-providers]` — typed FastAPI handlers wrapping common cloud-side patterns (Cloud Run Jobs forwarder, ECS `RunTask` forwarder, Lambda native-event handler). Trigger: first user request for provider glue code inside monet. Until then, users write the ~10-line forwarder themselves.
- **Long-running job suspend pattern** — `invoke_agent` currently stays alive waiting on `wait_completion` for the full `agent_timeout` duration regardless of pool type. Trigger: measured Aegra worker-thread pressure from jobs exceeding 5 minutes wall time.

### Priority 3 — Scheduled runs

Cron-style triggers that start runs against configured entrypoints on a schedule. Concrete scope:

- **Trigger records** — persisted schedule: `{name, entrypoint, input_template, cron_expr, enabled, last_run_at, next_run_at}`. Stored next to runs/deployments in the server's SQLite (or Postgres in production).
- **Scheduler process** — evaluates due triggers, dispatches via `MonetClient.run(entrypoint, input)`. Runs in-server as a background task (same lifecycle as worker cleanup sweeper), not a separate daemon. Single-writer lock to prevent duplicate firings when multiple server replicas exist.
- **CLI** — `monet schedule add|list|remove|run|enable|disable`. `monet schedule run <name>` dispatches out-of-band for manual testing.
- **Config vs. CRUD** — support both: `[schedules.<name>]` in `monet.toml` for declarative-at-boot schedules (survives redeploy); HTTP API for runtime CRUD (a downstream SaaS UI creates schedules per tenant).
- **Tenant scoping** — when Priority 1 lands, triggers carry `tenant_id`; the scheduler fires with that tenant in the request context.
- **Observability** — trigger firings emit a span so missed / late / overlapping fires are visible in tracing.

Out of scope here: human-friendly schedule editors, calendar UIs, retry semantics beyond the standard run lifecycle. Those live in downstream productization.

### Lower priority / triggered

- **Reference agent quality pass** — `src/monet/agents/` (planner, researcher, writer, qa, publisher) are functional but minimal: short prompts, thin tool use, limited signal coverage, no few-shot anchoring, no structured-output validation beyond basic pydantic. They are the first thing users read when copying patterns, so their quality sets the perceived ceiling of the SDK. Scope: improve prompting, broaden signal emission (cover `RECOVERABLE` / `AUDIT` groups, not just happy path), add few-shot examples, tighten output schemas, add retry-aware tool calls, document the decision in each agent module. Not a spec-gated change — incremental improvement as patterns are validated. Guardrail: do not promote these to "production-grade" reference implementations; they remain illustrative. Production agents live in user code. Three concrete migrations already specced under this umbrella: **researcher → GPT Researcher + constrained writer with source registry** (`docs/architecture/researcher-migration.md`, driven by the independent evaluation in `~/repos/agent-researcher`), **planner structured output via `with_structured_output` with validation-retry** (`docs/architecture/planner-structured-output.md`, exploratory), and **writer → section-level composite-document editing** (`docs/architecture/writer-migration.md`, context-engineering-driven; replaces single-shot `writer/deep` with `outline`/`draft_section`/`edit_section`/`compose`/`review_document` commands for long-form output).
- **AgentStream transport examples** — `AgentStream.cli() / .sse() / .http() / .http_post() / .sse_post()` constructors all ship today (`src/monet/streams.py:57-114`), but examples only cover `.cli()`. Add examples for (a) `.sse()` — browser or dashboard consuming a live agent's signals + progress via SSE; (b) `.http()` / `.http_post()` — webhook-driven agent where a callback URL delivers events to an external service. Each example should cover the full loop: agent emits, transport routes, external consumer renders. Lives under `examples/` alongside existing ones, respects the one-example-running-at-a-time lifecycle.
- **Queryable telemetry and meta-agents** — primitives that let agents (or external tooling) read completed-run telemetry: agent invocations, emitted signals, artifact pointers, wave timings, retry counts, token usage where captured. Unlocks two patterns explicitly: (a) **manager-agent** — one agent measures other agents' performance across runs, emits scores or escalations; (b) **self-learning agent** — an agent reads its own prior-run telemetry and adjusts behavior (e.g. prompt variants, tool selection). Decisions required before picking up: query surface (SDK helper vs. HTTP route vs. both), persistence source (OTel backend query vs. a monet-owned metrics store backed by SQLite/Postgres vs. both), whether agents get read-only access to other runs' artifact pointers, and the policy boundary for tenant-scoped queries once Priority 1 lands. Speculative until a concrete manager-agent prototype lands in `examples/` or a downstream project. Note: OTel spans are already emitted today; what's missing is a *queryable* surface agents can consume.
- **Graph extension points (slots)** — design deferred. Named, typed injection points in `entry` / `planning` / `execution` (plus an adapter-level `post_run`) that host user-supplied subgraphs. Covers the ultraplan pre-planner case and review-gate-with-replan loop. Full spec in `graph-extension-points.md`. Trigger: first concrete user request for injection at a specific published slot with a concrete subgraph to plug in. Phase 1 is adapter-level `post_run` only — cheapest validation of the model and directly solves the replan-loop case.
- **Pluggable pipeline adapters via `monet.toml`** — trigger: second adapter in-tree (see `## Deferred from client-decoupling refactor`).
- **In-process driver reintroduction (`_run.py`)** — trigger: concrete need for library-only usage (e.g. notebook example). See `## Deferred`. `src/monet/__main__.py` was deleted with `_run.py`; if a driver returns, `python -m monet` can be routed through it.
- **Graph ↔ client interrupt wire-contract test** — trigger: any change to `human_approval` / `human_interrupt` interrupt kwargs. See `## Deferred`.
- **E2E integration tests** across deployment topologies — see `## Unimplemented`.
- **Optional summarizer agent** — framework-inserted wave context condensation; see `docs/architecture/roadmap.md`.
- **Memory service** — first-class long-lived agent memory, peer of the artifact store. All agents can write memories; all agents receive relevant memories via hook injection or tool query. Memories are agent- and system-facing; artifacts are user-facing. Full design spec in `docs/architecture/memory-service.md`. Trigger: concrete user request for cross-run agent memory.

### Queue Phase 4 — deferred items

Deferred by the queue refactor (`queue-push-pull-system-update.md` v3) with explicit triggers. Each item is standalone; none blocks routine work.

- **Multi-replica Aegra completion handling** — today `result:{task_id}` strings are written by the single Aegra replica that handles each run. Trigger: second replica added — needs leader election or per-replica consumer-group splits for the `/pools/{pool}/claim` XREADGROUP so two replicas do not hand the same task to two workers.
- **JWT task tokens with `kid` + `exp`** — HMAC-derived bearers rotate with `MONET_API_KEY` (desired blast radius). Trigger: HMAC proves insufficient for cross-tenant revocation without rotating the whole API key.
- **`schema_version` envelope field** — `serialize_task_record` is a single-version blob. Trigger: first incompatible change to `TaskRecord` or `AgentResult` shape.
- **`MAXLEN` tuning from measurement** — `QueueConfig.work_stream_maxlen` defaults to unset (no trim). Trigger: first production observation at 100 users — pick numbers from observed `XLEN`, not round defaults.
- **`monet queue stats` / `monet queue reclaim` CLI inspectors** — operator-facing introspection. Trigger: first operator page for reclaim storm or completion backlog.
- **Backup / restore for `result:{task_id}` strings or stream contents** — TTL-bound strings self-expire; streams self-trim via `MAXLEN`. Trigger: a customer needs run replay across Redis primary failover.

## Refactor history

- **Client decoupling** (current HEAD): `_events.py` split into graph-agnostic core + `monet.pipelines.default.events`. `MonetClient.run(graph_id, input)` replaces the pipeline-composition `run(topic)`. HITL methods removed from `MonetClient`; typed verbs live in `monet.pipelines.default._hitl` as wrappers over `client.resume(run_id, tag, payload)`. `_run.py` deleted. `Entrypoint.kind` removed. See `docs/guides/client.md` and `docs/api/client.md`.