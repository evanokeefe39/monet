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
- [x] `configure_catalogue()`, `catalogue_from_env()` for backend wiring
- [x] `resolve_context()` for agent-side catalogue content resolution
- [x] `NeedsHumanReview`, `EscalationRequired`, `SemanticError` typed exceptions
- [x] `AgentDescriptor`, `CommandDescriptor`, `SLACharacteristics`, `RetryConfig` descriptors
- [x] OpenTelemetry tracing (spans, W3C traceparent, gen_ai.* conventions)
- [x] `monet.tracing` public module (configure_tracing, get_tracer, inject_trace_context, constants)
- [x] `CatalogueHandle` re-exported from top-level `monet` namespace

### Task Queue and Worker
- [x] `TaskQueue` protocol with pool-based claim (Prefect model)
- [x] `InMemoryTaskQueue` with per-pool queues, O(1) claim, backpressure, memory cleanup, cancellation
- [x] `SQLiteTaskQueue` with persistent storage, lease-based claiming, background sweeper
- [x] `run_worker()` with concurrent execution (semaphore-capped), OTel spans, graceful shutdown, optional registry
- [x] `bootstrap()` one-call server init (tracing → catalogue → manifest → queue → worker)

### Capability Manifest
- [x] `AgentManifest` static capability declaration with pool assignment
- [x] `@agent` auto-populates both registry and manifest
- [x] `_assert_registered` checks manifest (not handler registry) at graph build time
- [x] `invoke_agent` checks manifest before enqueue; `CAPABILITY_UNAVAILABLE` signal on missing agent

### Catalogue
- [x] `CatalogueClient` protocol
- [x] `ArtifactMetadata` model
- [x] `FilesystemStorage` + `SQLiteIndex` implementations
- [x] `CatalogueService` (composes storage + index)
- [x] `InMemoryCatalogueClient` for testing

### Orchestration
- [x] Three-graph supervisor topology: entry (triage) → planning (HITL) → execution (wave-based parallel)
- [x] Queue-only dispatch: `invoke_agent` enqueues to TaskQueue, polls for results
- [x] Pointer-only state: `_resolve_wave_result` passes summaries + catalogue pointers only
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

Deferred until there is concrete demand or a deployment requiring it.
- [ ] **Forwarding worker** — claims push-pool tasks, forwards to Cloud Run/ECS
- [ ] **Lease TTL + sweeper for push pools** — requeue crashed push tasks
- [ ] **Optional summarizer agent** — framework-inserted wave context condensation
