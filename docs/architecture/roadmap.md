# Roadmap

## Shipped

The following modules are implemented and tested:

### Core SDK (9 modules)
- [x] `@agent` decorator with parameter injection, auto-registration, content offload
- [x] `AgentResult`, `AgentRunContext`, `AgentSignals` types
- [x] Context entry types (discriminated union: artifact, work_brief, constraint, instruction, skill_reference)
- [x] `get_run_context()`, `get_run_logger()` context access
- [x] `write_artifact()`, `set_catalogue_client()`, `emit_progress()` utilities
- [x] `NeedsHumanReview`, `EscalationRequired`, `SemanticError` typed exceptions
- [x] `AgentDescriptor`, `CommandDescriptor`, `SLACharacteristics`, `RetryConfig` descriptors
- [x] `DescriptorRegistry` with thread-safe registration and test isolation
- [x] OpenTelemetry tracing (spans, W3C traceparent, gen_ai.* conventions)

### Catalogue (6 modules)
- [x] `CatalogueClient` protocol
- [x] `ArtifactMetadata` Pydantic model with PII/retention validation
- [x] `StorageBackend` protocol
- [x] `FilesystemStorage` implementation
- [x] `CatalogueService` (composes storage + SQLite index)
- [x] `InMemoryCatalogueClient` for testing

### Orchestration (5 modules)
- [x] `GraphState`, `AgentStateEntry` lean state schema
- [x] `create_node()` LangGraph node factory with HITL interrupt support
- [x] `invoke_agent()` transport-agnostic invocation (local + HTTP)
- [x] `build_retry_policy()` from descriptor config
- [x] `enforce_content_limit()` with catalogue offload

### Server (4 modules)
- [x] FastAPI application factory
- [x] Agent routes (`POST /agents/{agent_id}/{command}`)
- [x] Catalogue routes (`POST/GET /artifacts`)
- [x] Health route

## In progress

- [ ] **Supervisor graph topology** -- three-graph system (triage, planning, execution) with wave-based parallel execution. See [Graph Topology](graph-topology.md).

## Planned

- [ ] **Reference agents** -- five agents (planner, researcher, writer, QA, publisher) implemented with pi as the runtime
- [ ] **Skills system** -- versioned markdown files providing domain knowledge, loaded into agent context at invocation time
- [ ] **Extensions** -- lifecycle hooks (thinking, todo, context compression, tool result size) following pi's pattern
- [ ] **Langfuse integration** -- operational configuration for self-hosted Langfuse as OTel backend
- [ ] **Kaizen hook** -- unconditional post-execution reflection with hansei record
- [ ] **Work brief structure** -- structured plan artifact with phases, dependency waves, and quality criteria
- [ ] **Postgres checkpointing** -- durable graph execution state for production
- [ ] **Postgres metadata index** -- production catalogue index replacing SQLite

## Open design questions

| Item | Description |
|---|---|
| Skill store structure | Directory layout, naming conventions, loading mechanism |
| Extension interface | Precise hook signatures for the reference extensions |
| Base agent definitions | System prompts, toolsets, default extensions per reference agent |
