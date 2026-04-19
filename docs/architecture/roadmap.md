# Roadmap

## Shipped

### Core SDK
- [x] `@agent` decorator with dual call signature, parameter injection, auto-registration, content offload, pool assignment
- [x] `AgentResult`, `AgentRunContext`, `Signal` (TypedDict), `ArtifactPointer` types
- [x] `SignalType` vocabulary + `BLOCKING`/`RECOVERABLE`/`INFORMATIONAL`/`AUDIT`/`ROUTING` groups + `CAPABILITY_UNAVAILABLE`
- [x] `AgentStream` with `.cli()`/`.sse()`/`.http()` constructors, `.on()` handler builder
- [x] `webhook_handler` (with timeout + error handling), `log_handler` handler factories
- [x] `get_run_context()`, `get_run_logger()` context access
- [x] Ambient trio: `emit_progress()`, `emit_signal()`, `write_artifact()`
- [x] `configure_artifacts()`, `artifacts_from_env()` for backend wiring
- [x] `resolve_context()` for agent-side artifact store content resolution
- [x] `NeedsHumanReview`, `EscalationRequired`, `SemanticError` typed exceptions
- [x] `AgentDescriptor`, `CommandDescriptor`, `SLACharacteristics`, `RetryConfig` descriptors
- [x] OpenTelemetry tracing (spans, W3C traceparent, gen_ai.* conventions)
- [x] `monet.tracing` public module (configure_tracing, get_tracer, inject_trace_context, constants)
- [x] `ArtifactStoreHandle` re-exported from top-level `monet` namespace

### Task Queue and Worker
- [x] `TaskQueue` protocol with pool-based claim (Prefect model)
- [x] `InMemoryTaskQueue` with per-pool queues, O(1) claim, backpressure, memory cleanup, cancellation
- [x] `SQLiteTaskQueue` with persistent storage, lease-based claiming, background sweeper
- [x] `run_worker()` with concurrent execution (semaphore-capped), OTel spans, graceful shutdown, optional registry
- [x] `bootstrap()` one-call server init (tracing → artifact store → manifest → queue → worker)

### Capability Manifest
- [x] `AgentManifest` static capability declaration with pool assignment
- [x] `@agent` auto-populates both registry and manifest
- [x] `_assert_registered` checks manifest (not handler registry) at graph build time
- [x] `invoke_agent` checks manifest before enqueue; `CAPABILITY_UNAVAILABLE` signal on missing agent

### Artifact Store
- [x] `ArtifactClient` protocol
- [x] `ArtifactMetadata` model
- [x] `FilesystemStorage` + `SQLiteIndex` implementations
- [x] `ArtifactService` (composes storage + index)
- [x] `InMemoryArtifactClient` for testing

### Orchestration
- [x] Three-graph supervisor topology: entry (triage) → planning (HITL) → execution (wave-based parallel)
- [x] Queue-only dispatch: `invoke_agent` enqueues to TaskQueue, polls for results
- [x] Pointer-only state: `_resolve_wave_result` passes summaries + artifact store pointers only
- [x] Wave fan-out via LangGraph `Send`, QA reflection gates, retry budget
- [x] Signal routing: `SignalRouter` maps signal groups to actions (interrupt, retry)
- [x] State schemas: `EntryState`, `PlanningState`, `ExecutionState`, `WaveItem`, `WaveResult`

### Reference Agents
- [x] Planner (triage + work brief generation)
- [x] Researcher (fast + deep modes)
- [x] Writer (content generation)
- [x] QA (wave reflection evaluation)
- [x] Publisher (content publishing)

### Distribution Mode
- [x] **monet.toml** — declarative pool topology config with env var resolution
- [x] **FastAPI orchestration server** — `create_app()` factory with lifespan management
- [x] **Server API endpoints** — worker registration, heartbeat, task claim/complete/fail, deployments, health
- [x] **API key authentication** — Bearer token middleware for server endpoints
- [x] **Deployment records** — SQLite-backed storage for worker capability tracking
- [x] **monet worker CLI** — standalone process with AST discovery, heartbeat, local/remote modes
- [x] **monet register CLI** — CI/CD command for declaring remote agent deployments
- [x] **monet server CLI** — start the orchestration server with uvicorn
- [x] **AST agent discovery** — scan for @agent decorators without code execution
- [x] **WorkerClient** — HTTP client for remote worker ↔ server communication
- [x] **RemoteQueue** — TaskQueue adapter for remote workers
- [x] **monet.client module** — SDK client utilities (make_client, drain_stream, stream_run, state helpers, graph constants)

### Queue Providers
- [x] `InMemoryTaskQueue` — in-process, development and testing
- [x] `SQLiteTaskQueue` — persistent, single-server, lease-based claiming
- [x] `RedisTaskQueue` — standard Redis with pub/sub notifications + polling fallback
- [x] `UpstashTaskQueue` — HTTP-based serverless Redis, polling-only, key TTL cleanup

### Client SDK
- [x] `MonetClient` — typed async client with run lifecycle, event streaming, HITL decisions
- [x] Typed run events: `TriageComplete`, `PlanReady`, `PlanInterrupt`, `AgentProgress`, `WaveComplete`, `ReflectionComplete`, `ExecutionInterrupt`, `RunComplete`, `RunFailed`
- [x] Query types: `RunSummary`, `RunDetail`, `PendingDecision`
- [x] In-process `run()` async generator for local pipeline execution

### Worker Lifecycle
- [x] Worker heartbeat reconciliation — full capability sync on every heartbeat
- [x] Per-worker-id reconciliation via `AgentManifest.reconcile_worker()`
- [x] Stale worker cleanup — background sweeper removes dead workers' capabilities
- [x] `monet status` CLI command with `--flat` and `--json` output modes

### Core Restructure
- [x] Moved `_*` prefixed modules into `monet.core/` subpackage

## Planned

Items here are prioritized. Pick up as standalone plans. Triggers listed where applicable.

### Priority 1 — SaaS enabling primitives

SaaS platform (user management, accounts, billing, UI) lives in a separate downstream repo. This repo exposes only the primitives it needs. Scope: never grows a user model, billing logic, or customer-facing productization.

Queue plane already SaaS-compatible (all backends pull-only). Control-plane primitives to add:

- **Pluggable auth dependency** in `src/monet/server/_auth.py`: swap `MONET_API_KEY` singleton for a FastAPI dependency the downstream repo replaces. Default stays single-key for self-hosted.
- **Tenant ID as request-context primitive**: `TenantContext` propagated via `Depends`, opaque string.
- **Tenant-scoped queries**: runs, threads, artifacts, pending decisions filter by `tenant_id` when present — `src/monet/server/_routes.py`, `src/monet/client/_wire.py`, `src/monet/artifacts/_service.py`.
- **Credential passthrough on clients**: `MonetClient(url, api_key=...)` and `WorkerClient(api_key=...)` carry opaque bearer.
- **Server-side pool-claim validation** against tenant context — prevents cross-tenant task stealing.
- **Tenant-scoped stream keys** (`work:{tenant}:{pool}`) — trigger: Priority 1 lands. Current `work:{pool}` maps cleanly, one segment insertion.
- **Per-tenant rate limits on `/progress` and `/complete`** — trigger: Priority 1 lands.

### Priority 2 — Push pool dispatch (shipped) + follow-ons

**Shipped:** `src/monet/orchestration/_invoke.py` branches on `PoolConfig.type == "push"`, POSTs `{task_id, token, callback_url, payload}` to pool webhook URL via `httpx`. Workers run `monet worker --push` to stand up `POST /dispatch` endpoint. Auth is HMAC-derived per task (`HMAC_SHA256(MONET_API_KEY, task_id)`). Batch providers use `monet.core.push_handler.handle_dispatch(...)` in ~10-line user entry script.

Follow-ons (trigger-gated):

- **Retry / circuit breaker on provider API failures** — webhook POST 5xx raises `RuntimeError` today. Trigger: first observed transient throttling on real Cloud Run / Lambda.
- **Convenience provider extras** `monet[gcp]` / `monet[aws]` / `monet[azure]` / `monet[all-providers]`. Trigger: first user request for provider glue inside monet.
- **Long-running job suspend pattern** — `invoke_agent` stays alive waiting full `agent_timeout`. Trigger: measured Aegra worker-thread pressure from jobs > 5min.

### Priority 3 — Scheduled runs

Cron-style triggers against configured entrypoints. Scope:

- **Trigger records**: `{name, entrypoint, input_template, cron_expr, enabled, last_run_at, next_run_at}` — stored in server SQLite / Postgres.
- **Scheduler process**: evaluates due triggers, dispatches via `MonetClient.run(entrypoint, input)`. In-server background task, single-writer lock for replicas.
- **CLI**: `monet schedule add|list|remove|run|enable|disable`. `monet schedule run <name>` for manual dispatch.
- **Config vs. CRUD**: `[schedules.<name>]` in `monet.toml` for declarative-at-boot; HTTP API for runtime CRUD.
- **Tenant scoping**: triggers carry `tenant_id` once Priority 1 lands.
- **Observability**: trigger firings emit OTel spans for missed / late / overlapping fires.

Out of scope: schedule editors, calendar UIs, retry semantics beyond standard run lifecycle.

**Motivating use cases**: agent recruitment (discovery + trial pipelines), agent performance management (telemetry pipeline scoring agents on cost + quality). Scheduler is graph-agnostic — `monet schedule add --graph execution --input '<json>' --cron '<expr>'` is the only missing piece over the existing `examples/agent-recruitment/` reference implementation.

**Queue Phase 4 deferred items** (standalone, none blocks routine work):

- **Multi-replica Aegra completion handling** — trigger: second replica added.
- **JWT task tokens with `kid` + `exp`** — trigger: HMAC proves insufficient for cross-tenant revocation.
- **`schema_version` envelope field** — trigger: first incompatible change to `TaskRecord` / `AgentResult`.
- **`MAXLEN` tuning from measurement** — trigger: first production observation at 100 users.
- **`monet queue stats` / `monet queue reclaim` CLI** — trigger: first operator page for reclaim storm.
- **Backup / restore for stream contents** — trigger: customer needs run replay across Redis failover.

### Lower priority / triggered

- **Sandbox integration (Modal / E2B)** — `examples/agent-recruitment/src/recruitment/sandbox.py` is subprocess-based (not a security boundary). Ship `modal_sandbox.py` / `e2b_sandbox.py` implementing same signature. Trigger: first user running recruitment pipeline against untrusted candidates.
- **Chat auto-open artifact links** — `/autolink on|off` TUI command, regex detection of `…/api/v1/artifacts/<id>/view`, `webbrowser.open_new_tab()`. Default off.
- **Agentic chat reference graph** — `examples/agentic-chat/` with `build_chat_agentic_graph` + `conversationalist` reference agent. Opt-in via `MONET_CHAT_GRAPH` or `[chat] graph = "..."`.
- **Reference agent quality pass** — `src/monet/agents/` functional but minimal. Improve prompting, broaden signal coverage, add few-shot anchoring, tighten output schemas. Three concrete migrations specced: researcher → GPT Researcher (`architecture/researcher-migration.md`), planner structured output (`architecture/planner-structured-output.md`), writer → section-level editing (`architecture/writer-migration.md`).
- **AgentStream transport examples** — add SSE and HTTP/webhook examples. All five constructors ship in `src/monet/streams.py:57-114`; only `.cli()` has an example today.
- **Queryable telemetry and meta-agents** — queryable surface over OTel spans for manager-agent and self-learning patterns. Prereq decisions: query surface, persistence source, cross-run artifact access, tenant-scope boundary.
- **Graph extension points (slots)** — named injection points in `planning` / `execution` + adapter-level `post_run`. Full spec in `docs/architecture/graph-extension-points.md`. Trigger: first concrete user request for a specific slot. Phase 1: `post_run` only.
- **In-process driver reintroduction** — trigger: concrete server-less library use case. `src/monet/__main__.py` deleted with `_run.py`; driver should use `build_default_graph` directly.
- **E2E integration tests** across deployment topologies — scaffold in `tests/e2e/`; fill scenarios as topologies stabilise.
- **Optional summarizer agent** — framework-inserted wave context condensation.
- **Memory service** — long-lived agent memory, peer of artifact store. Full design in `docs/architecture/memory-service.md`. Trigger: concrete cross-run memory request.

## Refactor History

- **Three-graph collapse** (current HEAD): `entry` / `planning` / `execution` become uncompiled subgraphs composed under one `StateGraph[RunState]` via `build_default_graph`. `monet.pipelines.default` adapter (~350 LoC) deleted. See `docs/architecture/adr-001-collapse-three-graph-split.md`.
- **Client decoupling** (prior HEAD): `MonetClient.run(graph_id, input)` replaced pipeline-composition `run(topic)`. `_run.py` and `Entrypoint.kind` removed. `_events.py` split into graph-agnostic core + per-pipeline events.
