# Chat REPL + Graph Collapse Plan

Five tracks. Track A ships standalone. Tracks B/C/D/E ordered.

Design decisions (locked):

- **Collapse** three graphs via **subgraph-as-node** under one top-level `StateGraph[RunState]`. Idiomatic LangGraph.
- **Slim public `RunState`** (~8 fields). Private `EntryState` / `PlanningState` / `ExecutionState` stay in their subgraph modules.
- **User extension via `MyRunState(RunState, total=False)`** + new nodes. TypedDict inheritance + LangGraph preserves unknown keys across subgraph boundaries. OCP by construction.
- **`RunState` versioning**: package version = contract version. No embedded version field. Minor = additive, major = breaking.
- **Interrupts: form-schema convention.** Graph emits dict with `prompt`, `fields[]`, `context`. CLI renders field types (`text`, `textarea`, `radio`, `checkbox`, `select`, `int`, `bool`, `hidden`). `Form`+`Field` TypedDicts opt-in, no pydantic at protocol level, no parser, no resume builder. Graceful fallback to raw display when `fields` absent.
- **Delete typed interrupt layer**: `PlanInterrupt`, `ExecutionInterrupt`, all interrupt/decision TypedDicts, `DefaultInterruptTag`, `_hitl.py` verbs, adapter projections.
- **Rendering lives in `monet.cli._render`**, not in pipelines.
- **Private subgraph↔parent mappers.**
- **Flat interrupt tag names**, no namespace.
- **Pluggable adapter roadmap item retired** when Track B lands.

Open engineering spikes (inside Track B, not design open):

- LangGraph checkpoint-before-edge ordering — crash mid-transition recoverable?
- Concurrent `client.resume` semantics on compound graph — confirm `RunNotInterrupted` / `AlreadyResolved` cover compound case.
- Progress-sequence field for dedupe on resume.

---

## Track A — stub bug + CLI decoupling (no refactor)

No API break. Unblocks independent of B.

- [ ] Add `continue_after_plan_approval(client, run_id)` async generator in `src/monet/pipelines/default/adapter.py`
  - Reads planning thread state post-approval
  - Extracts `work_brief_pointer` + `routing_skeleton`
  - Yields `PlanApproved` + `PlanReady`
  - Calls existing `_drive_execution`
- [ ] Rewire `cli/_run.py:_resume_pipeline` to iterate `continue_after_plan_approval`, handle `ExecutionInterrupt` / `RunComplete` / `RunFailed` / errors — mirror main loop
- [ ] Remove `is_default` branching at `cli/_run.py:134`. Dispatch becomes data-driven: if entrypoint has `adapter` key (added in B) use adapter, else single-graph. Pre-B, every entrypoint is single-graph except `default` which still hardcodes. Acceptable.
- [ ] `monet chat --graph <entrypoint>` honors `monet.toml [entrypoints]`, not just graph-role map
- [ ] Drop `/run` and `/attach` slash commands from `cli/_chat.py` (default-pipeline-coupled)
- [ ] Update `/help` output, rewrite slash-command reference in `docs/`
- [ ] Test: manual approve path drives execution to completion
- [ ] Test: all 393 existing tests pass
- [ ] Commit

## Track D (scaffold) — E2E harness baseline

Before B lands. Captures current behavior as regression baseline.

- [ ] New directory `tests/e2e/` with `conftest.py`
- [ ] Fixture: `monet_dev_server` — subprocess `monet dev`, health-probe, teardown via `monet dev down`
- [ ] Fixture: `docker_postgres` — spin up / tear down Postgres via compose
- [ ] Add `@pytest.mark.e2e` marker to `pyproject.toml`
- [ ] CI: separate job, opt-in via `pytest -m e2e`
- [ ] E2E-01: `monet run "topic" --auto-approve` happy path against default pipeline
- [ ] E2E-02: `monet run "topic"` manual HITL — approve / revise / reject
- [ ] Commit baseline before B touches anything

## Track B — subgraph-as-node collapse

One PR per ST-18 (docs + code + tests land together).

### B.1 State schema

- [ ] Define `RunState` TypedDict in `src/monet/orchestration/_state.py` (new exported name). Fields: `task`, `run_id`, `trace_id`, `triage`, `work_brief_pointer`, `routing_skeleton`, `wave_results` (Annotated reducer), `abort_reason`
- [ ] Keep `EntryState`, `PlanningState`, `ExecutionState` as private (drop from `__all__` if exported)
- [ ] Export `RunState` from `monet.orchestration.__init__`
- [ ] Test: `RunState` passes mypy strict
- [ ] Test: `MyRunState(RunState, total=False)` with extra field compiles + round-trips

### B.2 Subgraph composers

- [ ] Rename `build_entry_graph` → `build_entry_subgraph` (returns uncompiled `StateGraph[EntryState]`)
- [ ] Rename `build_planning_graph` → `build_planning_subgraph`
- [ ] Rename `build_execution_graph` → `build_execution_subgraph`
- [ ] Each subgraph module: add private `_map_from_parent(run: RunState) -> PhaseState` + `_map_to_parent(phase: PhaseState) -> RunState` helpers
- [ ] Export subgraph composers from `monet.orchestration.__init__`
- [ ] Keep `build_chat_graph` as-is (messages-reducer, different shape)
- [ ] Test: each subgraph compiles standalone

### B.3 Compound default graph

- [ ] New `src/monet/orchestration/default_graph.py`: `build_default_graph() -> StateGraph[RunState]`
  - `g.add_node("entry", build_entry_subgraph().compile())`
  - `g.add_node("planning", build_planning_subgraph().compile())`
  - `g.add_node("execution", build_execution_subgraph().compile())`
  - `START → entry → planning → execution → END`
- [ ] Update `src/monet/server/default_graphs.py` — `build_default_graph()` replaces separate entry/planning/execution wrappers
- [ ] Update `aegra.json`: remove `entry`, `planning`, `execution` entries, add `default`. Keep `chat`.
- [ ] Update `DEFAULT_GRAPH_ROLES` in `config/_graphs.py`: `{"default": "default", "chat": "chat"}` — remove `entry`/`planning`/`execution` since they're internal
- [ ] Update `DEFAULT_ENTRYPOINTS`: `{"default": {"graph": "default"}}`
- [ ] Test: compound graph runs end-to-end against fake LangGraph SDK
- [ ] Test: checkpoint survives interrupt at planning HITL
- [ ] Test: checkpoint survives process restart mid-execution (durability)

### B.4 Form-schema interrupt convention

- [ ] Add `Field` + `Form` TypedDicts to `src/monet/client/_events.py` (opt-in type hints)
- [ ] Document field-type vocabulary in `docs/api/state.md`: `text`, `textarea`, `radio`, `checkbox`, `select`, `int`, `bool`, `hidden` — required keys per type, optional keys (`label`, `default`, `required`, `help`), envelope keys (`prompt`, `fields`, `context`)
- [ ] Rewrite `human_approval_node` in `planning_graph.py` to emit form-schema dict via `interrupt(...)`. Read `decision["action"]` + optional `decision["feedback"]` from resume payload.
- [ ] Rewrite `human_interrupt` in `execution_graph.py` to emit form-schema dict. Read `decision["action"]`.
- [ ] `render_interrupt_form(values: dict) -> dict` in `src/monet/cli/_render.py`:
  - If no `fields`, dump raw + prompt for JSON
  - Else walk fields, dispatch to per-type renderer (click.prompt / click.confirm / numbered menu)
  - Return resume payload dict
- [ ] Wire `cli/_run.py` + `cli/_chat.py` to call `render_interrupt_form` on `Interrupt` events
- [ ] Test: each field type renders + round-trips payload
- [ ] Test: malformed dict falls back to raw render
- [ ] Test: planning HITL happy path with form schema

### B.5 Deletions

- [ ] Delete `src/monet/pipelines/default/adapter.py`
- [ ] Delete `src/monet/pipelines/default/_hitl.py`
- [ ] Delete `src/monet/pipelines/default/_inputs.py`
- [ ] Delete `src/monet/pipelines/default/events.py` (`PlanInterrupt`, `ExecutionInterrupt`, `TriageComplete`, `PlanReady`, `PlanApproved`, `WaveComplete`, `ReflectionComplete`, `DefaultInterruptTag`, `DefaultPipelineRunDetail`, all `*Values` / `*Payload` TypedDicts)
- [ ] Delete `src/monet/pipelines/default/render.py` — move any still-useful helpers to `cli/_render.py`
- [ ] Delete `src/monet/pipelines/default/__init__.py` — empty the package
- [ ] Remove `monet.pipelines.default` from `tests/test_public_api.py` pins
- [ ] Delete `tests/test_default_pipeline_events.py` (replaced by B.3/B.4 tests)
- [ ] Remove dead imports across `cli/_run.py`, `cli/_chat.py`

### B.6 Reliability gates

- [ ] Spike: LangGraph checkpoint-before-edge — write failing test that crashes mid-transition and attempts recovery. If LangGraph doesn't guarantee this, escalate before proceeding.
- [ ] Spike: concurrent `client.resume` — two clients race, confirm `RunNotInterrupted` / `AlreadyResolved` error on second. If not, add single-writer enforcement.
- [ ] Add `sequence: int` field to `emit_progress` events in `core/stubs.py` for dedupe on resume
- [ ] Test: resume after simulated stream disconnect — no duplicate event effects
- [ ] Add per-node error-routing edges in compound graph where applicable (bulkhead — exception in one subgraph routes to failure node, not whole-run crash)

### B.7 Docs + ADR (same PR)

- [ ] New ADR `docs/architecture/adr-001-collapse-three-graph-split.md` — reversal rationale, subgraph-as-node choice, private phase state
- [ ] New `docs/api/state.md` — `RunState` fields, versioning policy (package-version = contract-version), field-type vocabulary for interrupts, `MyRunState(RunState)` extension pattern example
- [ ] Rewrite `docs/api/client.md` — remove `approve_plan` / `revise_plan` / HITL verb sections, add generic `client.resume(run_id, tag, payload)` pattern
- [ ] Rewrite `docs/guides/client.md` — single-pipeline story
- [ ] Update `CLAUDE.md` Layout section — remove `pipelines/default/` subpackage, add `orchestration/default_graph.py` + subgraph composer API
- [ ] Update `CLAUDE.md` Refactor history — append entry explaining collapse
- [ ] Retire "pluggable pipeline adapters via `monet.toml`" from `## Roadmap` in `CLAUDE.md` + `docs/architecture/roadmap.md` with note pointing at subgraph composition
- [ ] Retire "In-process driver reintroduction" item if appropriate (still trigger-based)
- [ ] Remove `kind` field remnants in docs

## Track D (extend) — E2E scenarios post-collapse

- [ ] E2E-03: `monet chat` send → response; server restart via `monet dev down` + `monet dev`; `/history` preserves conversation
- [ ] E2E-04: user-provided `aegra.json` with custom graph; `monet run <custom> "task"` streams and completes
- [ ] E2E-07: interrupt + server restart + resume — checkpoint durability
- [ ] E2E-08: worker reconnection after server restart
- [ ] E2E-09: Redis queue backend HITL parity vs memory backend — same resume behavior
- [ ] E2E-10: user extends `build_default_graph` with custom review node via `MyRunState`; `monet run` drives compound graph + custom node + custom events surface in CLI

## Track C — custom agents in chat + direct invocation

Depends on A (chat decoupling) and B (no adapter coupling).

- [ ] Add `GET /api/v1/agents` endpoint in `src/monet/server/_aegra_routes.py` — returns manifest entries
- [ ] `MonetClient.list_capabilities() -> list[Capability]` — wraps the endpoint
- [ ] `monet chat` REPL: on start, call `list_capabilities()`, build slash dispatch map `{f"/{agent_id}:{command}": ...}`
- [ ] `/help` lists all `/<agent_id>:<command>` shortcuts
- [ ] `/refresh` slash command reloads manifest
- [ ] Colon in first arg of `monet run` routes to direct-agent path via `invoke_agent`. Emit `AgentResult` to CLI renderer (output + signals + artifacts)
- [ ] Chat direct-agent invocation: render `AgentResult` inline, append summary to thread via `send_context`
- [ ] Tests for manifest endpoint + client method + REPL dispatch

## Track E — examples

Depend on respective prior tracks.

- [ ] **E.1 `examples/chat-default/`** (after A) — `pyproject.toml`, `README.md`, `.env.example`. Demonstrates stock `monet chat`, session resume, `/help`, `/history`, `/name`.
- [ ] **E.2 `examples/chat-extended/`** (after C) — custom `@agent("search")`, `@agent("report_writer")`. Shows `/search:fast`, `/report_writer:draft`, `monet run search:fast "query"`, planning roster inclusion.
- [ ] **E.3 `examples/custom-pipeline/`** (after B) — `MyRunState(RunState)` + custom review node composed with `build_*_subgraph()`. `monet.toml [entrypoints.reviewed]`. Demonstrates OCP extension: retires pluggable-adapter roadmap item concretely.

## Review

_To be filled in at end of each track._
