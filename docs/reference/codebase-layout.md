# Codebase Layout

## `src/monet/` — package source (src layout)

**Top-level modules (stable public surface)**
- `types.py` — `AgentResult`, `AgentRunContext`, `Signal`, `ArtifactPointer`, `build_artifact_pointer`, `find_artifact`
- `signals.py` — `SignalType` vocabulary + group frozensets
- `streams.py` — `AgentStream`
- `handlers.py` — stream handler factories
- `exceptions.py` — `SemanticError`, `EscalationRequired`, `NeedsHumanReview`
- `tracing.py` — public tracing API
- `agent_manifest.py` — `configure_agent_manifest`, `get_agent_manifest` (orchestration-side)
- `_ports.py` — canonical local ports (`STANDARD_POSTGRES_PORT=5432`, `STANDARD_REDIS_PORT=6379`, `STANDARD_DEV_PORT=2026`, `STANDARD_LANGFUSE_PORT=3000`) and `state_file()` helper

**`config/`** — central configuration subpackage
- `_env.py` — single boundary where SDK touches `os.environ`: registers every `MONET_*` name as `Final[str]`, exposes typed accessors `read_str/bool/float/int/path/enum`, defines `ConfigError`
- `_load.py` — owns `monet.toml` reading: `default_config_path`, `read_toml`, `read_toml_section`
- `_schema.py` — per-unit pydantic schemas: `ServerConfig`, `WorkerConfig`, `ClientConfig`, `ObservabilityConfig`, `ArtifactsConfig`, `QueueConfig`, `AuthConfig`, `OrchestrationConfig`, `ChatConfig`, `CLIDevConfig` — each with `load()`, `validate_for_boot()`, `redacted_summary()`. `ChatConfig.graph` is a `module.path:factory` dotted reference resolved at server boot.
- `_graphs.py` — loads `monet.toml [graphs]` role mapping and `[entrypoints.<name>]` declarations. `DEFAULT_ENTRYPOINTS` + `load_entrypoints()` gate which graphs `MonetClient.run` / `monet run` can invoke. `Entrypoint` is a one-field `TypedDict({"graph": str})`. Every deployable unit validates config at boot and emits a redacted summary at `INFO`. See `docs/reference/env-vars.md` for the full registry.

**`core/`** — worker-side internals
- `decorator.py` — `@agent`
- `registry.py` — handler registry
- `manifest.py` — capability declarations
- `context.py` — `contextvars` run context
- `context_resolver.py` — `resolve_context` (artifact store read)
- `tracing.py` — OTel setup
- `hooks.py` — `GraphHookRegistry`, `@on_hook`
- `stubs.py` — `emit_progress` / `emit_signal` / `write_artifact`
- `artifacts.py` — worker-side handle
- `worker_client.py`, `_agents_config.py`, `_retry.py`, `_serialization.py`

**`cli/`** — click commands
- `_dev.py` — `monet dev` as a group with `down` subcommand + auto-teardown
- `_run.py` — single generic streaming path for every entrypoint; form-schema interrupt prompting
- `_runs.py` — list / pending / inspect / resume; `resume` renders the pending interrupt's form schema
- `_chat.py` — thin Click entry: resolves thread, fetches slash commands + history, configures per-thread file logging to `.cli-logs/<timestamp>_<thread_id>.log` when `--verbose` is set, hands off to Textual
- `chat/` subpackage:
  - `_app.py` — Textual `ChatApp`: `RichLog` transcript with `_styled_line` role tag colours, toolbar, `RegistrySuggester` ghost-text, dropdown `OptionList` for slash completions with `tab` accept, `_PickerScreen`, `SidebarPanel` (ctrl+b) with agents / threads / artifacts / keys tabs, `MainMenuScreen` (ctrl+p) replacing built-in command palette — submenus: options/theme, keyboard shortcuts, command library, about. TUI-local commands `/new` `/clear` `/threads` `/switch` `/agents` `/artifacts` `/runs` `/help` `/quit` `/exit`. HITL interrupts render as transcript text; next user submission parsed as resume payload. `_format_form_prompt` + `_parse_text_reply` handle three shapes: approval (`approve | revise <feedback> | reject`), single-field, multi-field (one line per field).
  - `_sidebar.py` — right-docked `SidebarPanel` with four `TabbedContent` tabs; min width `FLOOR_COLS=50`
  - `_menu.py` — `MainMenuScreen` + sub-screens: `OptionsScreen`, `KeyboardShortcutsScreen`, `CommandLibraryScreen`, `AboutScreen`
  - `_turn.py` — `run_turn` / `drain_stream` / `empty_stream` / `InterruptCoordinator`
- `_worker.py` — remote workers register via first heartbeat (no separate `monet register` command)
- `_server.py`, `_status.py`
- `_render.py` — Click-based `render_event`, `render_interrupt_form` walking form-schema vocabulary `text/textarea/radio/checkbox/select/int/bool/hidden` (used by `_run.py` and `_runs.py`)
- `_discovery.py`, `_setup.py`
- `_db.py` — `monet db migrate | current | stamp | check`

**`client/`**
- `__init__.py` — `MonetClient` (graph-agnostic)
- `_events.py` — core events: `RunStarted`, `NodeUpdate`, `AgentProgress`, `SignalEmitted`, `Interrupt`, `RunComplete`, `RunFailed`, plus `RunSummary`, `RunDetail`, `PendingDecision`, `ChatSummary`, plus form-schema interrupt convention `Form` / `Field` / `FieldOption` TypedDicts
- `_errors.py` — `MonetClientError` + `RunNotInterrupted`, `AlreadyResolved`, `AmbiguousInterrupt`, `InterruptTagMismatch`, `GraphNotInvocable`, `ServerUnreachable`, `ServerError`
- `_wire.py` — `task_input`, `chat_input`, `stream_run`, `get_state_values`, `create_thread`, metadata keys, `_classify_transport_error` maps httpx errors to typed `MonetClientError` subclasses
- `_run_state.py` — run-state cache `{run_id: {graph_id: thread_id}}`

**`hooks/`** — built-in graph hooks
- `plan_context.py` — `inject_plan_context` worker hook that resolves `work_brief_pointer` + `node_id` into task content

**`queue/`** — `TaskQueue` protocol + `_worker.run_worker` + backends: `memory`, `redis`, `sqlite`, `upstash`

**`orchestration/`** — flat-DAG subgraphs and the compound default graph
- Public surface: `RunState` (slim parent state, designed for `MyRunState(RunState)` extension), `build_planning_subgraph` / `build_execution_subgraph` (uncompiled `StateGraph`s for composing custom pipelines), `build_default_graph` (composes planning + execution as nodes under `RunState`), `build_chat_graph` (slash-parse → binary triage `{chat, plan}` → planner / questionnaire / approval, then on approve mounts the same `build_execution_subgraph` as a node + `execution_summary_node`)
- Triage is information-only (returns route + confidence; does not pick an agent)
- `planner_node` makes one agent call per visit, writes either `last_plan_output` or `pending_questions` into `ChatState`, plus `work_brief_pointer` (extracted via `find_artifact("work_brief")`) and `routing_skeleton`
- `questionnaire_node` renders `pending_questions` as one `text` field per question; TUI parses one line per field, `skip`/empty drops that answer
- `approval_node` interrupts with approve/revise/reject; revise writes `plan_feedback` → back to planner; approve → execution; reject → terminate
- Bounds: `PLAN_MAX_REVISIONS=3`, `MAX_FOLLOWUP_ATTEMPTS=1`
- Reference planner emits `{kind:"plan"}` + WorkBrief or `{kind:"questions"}` + `NEEDS_CLARIFICATION`; orchestrator routes on signal, not prose
- `_invoke_planner` injects `agent_roster` context from server-side `CapabilityIndex` (ADR-004)
- Private: `_state.py` (state schemas, `RoutingSkeleton`/`RoutingNode` pydantic models), `_invoke.py` (queue-only dispatch), `_signal_router.py`, `_validate.py`

**`server/`**
- `create_app()` — FastAPI factory + ASGI lifespan
- `_aegra_routes.py` — lifespan calls `bootstrap_server()` (single queue-creation site), registers reference agents, heartbeats `monolith-0` in-process worker, spawns `run_worker` against shared queue
- `_capabilities.py` — authoritative server-side `CapabilityIndex` populated by worker heartbeats; pydantic `Capability` wire model validates charset / length at boundary
- `_langgraph_config.py` — langgraph config generation
- `server_bootstrap.py` — 0-arg Aegra wrappers for `build_chat_graph`, `build_default_graph`, `build_execution_graph`; `bootstrap_server()` idempotent queue factory (call once from lifespan, never from module body); promotes `monet` logger to INFO at boot (respects `MONET_LOG_LEVEL` override)
- `_auth.py` — `require_api_key` is no-op when `MONET_API_KEY` unset (keyless dev); strict Bearer check when key configured. `require_task_auth` always requires key (HMAC derivation)
- `_routes.py` — `POST /api/v1/workers/{worker_id}/heartbeat`, `POST /api/v1/pools/{pool}/claim`, `GET /api/v1/agents`, `GET /api/v1/artifacts/{id}` / `{id}/view`

**`agents/`** — reference agents using partial form: planner, researcher, writer, qa, publisher

**`artifacts/`** — artifact store: index, memory, metadata, storage, protocol, service, `artifacts_from_env()` helper
- `_migrations.py` — programmatic alembic entry points: `apply_migrations`, `check_at_head`, `head_revision`, `stamp_head`
- `artifacts_from_env()` auto-migrates for dev ergonomics; production deploys gate with `monet db check` + construct `SQLiteIndex` directly

**`_migrations/`** — package-shipped alembic tree (`env.py`, `script.py.mako`, `versions/`). Sync-only (no asyncio loops). Baseline `0001_baseline` creates `artifacts` table.

## `tests/`

- `_fakes.py` — fake LangGraph SDK client
- `test_default_compound_graph.py` — compound graph (planning-interrupt pause, approve-and-drive, reject-halts-pipeline, no-entry-node shape)
- `test_chat_graph.py` — every chat-graph node + conditional routing
- `test_subgraph_composition_spike.py` — LangGraph properties subgraph composition depends on
- `test_public_api.py` — public surface of `monet`, `monet.client`, `monet.orchestration`
- `tests/e2e/` — opt-in E2E tests behind `e2e` pytest marker (set `MONET_E2E=1`)

## `docs/`

mkdocs-material documentation.
