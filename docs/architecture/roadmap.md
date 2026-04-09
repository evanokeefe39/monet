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

### Task Queue and Worker
- [x] `TaskQueue` protocol with pool-based claim (Prefect model)
- [x] `InMemoryTaskQueue` with per-pool queues, O(1) claim, backpressure, memory cleanup, cancellation
- [x] `run_worker()` with concurrent execution (semaphore-capped), OTel spans, graceful shutdown
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

### Server
- [x] FastAPI application factory with agent and catalogue routes

## Planned (Distribution Mode)

Deferred until there is a deployment beyond single-server monolith. The queue protocol and pool abstractions support this without architecture changes.

- [ ] **Queue API endpoints** — FastAPI routes for remote worker claim/complete/fail
- [ ] **monet.toml** — declarative pool topology config
- [ ] **monet worker CLI** — standalone process with AST discovery, heartbeat, poll loop
- [ ] **Forwarding worker** — claims push-pool tasks, forwards to Cloud Run/ECS
- [ ] **Lease TTL + sweeper** — requeue crashed tasks
- [ ] **monet register CLI** — CI/CD command for declaring remote agent deployments
- [ ] **Redis/Upstash queue backend** — persistent TaskQueue for cross-process workers
- [ ] **monet.client module** — SDK client utilities (drain_stream, get_state_values, state initializers)
- [ ] **Optional summarizer agent** — framework-inserted wave context condensation
