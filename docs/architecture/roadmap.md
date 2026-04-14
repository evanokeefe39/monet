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

Deferred until there is concrete demand or a deployment requiring it.
- [ ] **Forwarding worker** — claims push-pool tasks, forwards to Cloud Run/ECS
- [ ] **Lease TTL + sweeper for push pools** — requeue crashed push tasks
- [ ] **Optional summarizer agent** — framework-inserted wave context condensation
- [ ] **Graph extension points (slots)** — named, typed injection points in `entry` / `planning` / `execution` plus an adapter-level `post_run`. Hosts user-supplied subgraphs with typed state contract and optional decision-based routing (e.g. `replan` loops back to planning). Covers the ultraplan pre-planner and review-gate-with-replan cases. Design in `graph-extension-points.md`. Trigger: first concrete user request for a specific published slot. Phase 1 ships the adapter-level `post_run` slot alone.
- [ ] **Scheduled runs** — cron-style triggers that start runs against configured entrypoints on a schedule. Persisted trigger records, in-server scheduler (single-writer lock for replicas), CLI (`monet schedule add|list|remove|run|enable|disable`), `[schedules.<name>]` config block plus runtime HTTP CRUD. Tenant-scoped once Priority 1 lands. Trigger firings emit OTel spans for missed/late/overlap visibility.
- [ ] **Queryable telemetry for meta-agents** — primitives that let agents read completed-run telemetry (agent invocations, signals, artifact pointers, wave timings, retry counts, token usage where captured). Unlocks manager-agent patterns (one agent measures others' performance across runs) and self-learning agents (agent reads its own prior runs to refine behavior). Prereq decisions: query surface (SDK helper vs. HTTP route), persistence source (OTel backend vs. monet-owned metrics store vs. both), cross-run artifact read access, tenant-scope boundary. OTel spans already emit — this feature is about making them queryable from inside an agent.
- [ ] **Reference agent quality pass** — `src/monet/agents/` are functional but minimal. Improve prompting, broaden signal coverage (`RECOVERABLE` / `AUDIT`, not just happy path), add few-shot anchoring, tighten output schemas, document decisions. Incremental, not spec-gated. Guardrail: keep them illustrative, not production-grade. Three concrete migrations specced: **researcher → GPT Researcher + constrained writer** (`architecture/researcher-migration.md`), **planner structured output with validation-retry** (`architecture/planner-structured-output.md`), and **writer → section-level composite-document editing** (`architecture/writer-migration.md`).
- [ ] **AgentStream transport examples** — examples for `AgentStream.sse()` (browser/dashboard consuming live signals) and `.http()` / `.http_post()` (webhook-driven agent with external consumer). Today only `.cli()` is demonstrated despite all five constructors shipping in `src/monet/streams.py:57-114`.
- [ ] **Memory service** — first-class long-lived agent memory, peer of the artifact store. All agents can write memories; all agents receive relevant memories via hook injection or tool query. Memories are agent- and system-facing; artifacts are user-facing. Separate service sharing storage backend with artifacts, divergent index / retrieval / TTL. Full design, taxonomy rationale, and open-questions list in `architecture/memory-service.md`. Trigger: concrete user request for cross-run memory (e.g. an agent that remembers prior task feedback across sessions).
