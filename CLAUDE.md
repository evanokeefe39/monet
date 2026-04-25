# CLAUDE.md

## Project

monet = multi-agent orchestration SDK for Python. MIT, solo maintainer. SDK has `@agent` decorator with pool assignment, typed context injection, artifact store, LangGraph orchestration with task queue, FastAPI server layer. Keep minimal and clean.

Known issues (bugs, deprecations, standards violations, design gaps) in `ISSUES.md`. Roadmap in `docs/architecture/roadmap.md`. Check `ISSUES.md` before maintenance work — no duplicate or paper-over of listed issues without explicit scope.

## Commits
Always use `caveman:caveman-commit` skill for commit subject + body. No hand-written commit messages. Invoke via Skill tool (`skill: "caveman:caveman-commit"`) before `git commit`.

## Layout

See `docs/reference/codebase-layout.md` for full per-module descriptions.

Top-level dirs:
- `src/monet/` — package source (src layout): `events/`, `config/`, `core/`, `cli/`, `client/`, `hooks/`, `progress/`, `queue/`, `worker/`, `orchestration/`, `server/`, `agents/`, `artifacts/`, `_migrations/`
- `tests/` — pytest tests; `tests/e2e/` opt-in E2E behind `e2e` marker
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

Runtime: pydantic, opentelemetry-api/sdk, sqlalchemy, fastapi, uvicorn, langgraph, textual.
Dev: pytest, pytest-asyncio, hypothesis, httpx, ruff, mypy, mkdocs-material, pre-commit.

## Code standards

- Python 3.12+, type annotations on all public API
- `py.typed` marker present — maintain inline types
- mypy strict, zero errors required
- ruff for lint + format, line length 88
- Pre-commit hooks enforce ruff and mypy before each commit

## Testing
- Be frugal with testing, full runs only when it makes sense or pre commit.
- All tests in `tests/` via pytest, async mode auto
- Test files named `test_*.py`
- Every public function needs a test
- conftest.py provides autouse `_queue_worker` fixture: creates InMemoryTaskQueue + background worker for all async tests. invoke_agent works transparently.

- Always run tests with `-q 2>&1 | tail -60` so the pass/fail summary is visible in one shot. Never use `-v` without a tail pipe — output truncates and you will not know if tests passed without running again.

## Code navigation

SymDex MCP installed, repo registered (`~/.symdex/monet.db`). Tools **deferred** — schemas not loaded by default. For ANY symbol lookup, callgraph trace, file outline, or semantic search against `src/monet/**`, **MUST** load schemas first via `ToolSearch`:

```
query: "select:mcp__symdex__search_symbols,mcp__symdex__semantic_search,mcp__symdex__get_callers,mcp__symdex__get_callees,mcp__symdex__get_file_outline,mcp__symdex__get_repo_outline,mcp__symdex__search_routes,mcp__symdex__get_index_status"
```

Then use tools. Fall back to Read/Grep/Glob on `src/` only when symdex returns nothing. Full-file `Read` correct for non-code files (toml/md/json/yaml) and when complete file context needed. `symdex-code-search` skill documents full tool surface.

Tool map:

- `mcp__symdex__search_symbols` / `get_symbol` — functions, classes, methods by name with exact byte offsets
- `mcp__symdex__semantic_search` — code by intent, not exact name
- `mcp__symdex__get_callers` / `get_callees` — call graph trace
- `mcp__symdex__get_file_outline` / `get_repo_outline` — structure without reading full files
- `mcp__symdex__search_routes` — HTTP endpoints across codebase
- `mcp__symdex__get_index_status` — freshness at session start; reindex via `index_repo` if stale (watcher off by default)

## Style

- src layout, all imports from `monet`
- No unnecessary abstractions or speculative code
- Keep dependencies minimal — justify new additions
- Docstrings on public API only, Google style

## CI

GitHub Actions runs ruff, mypy, pytest on push/PR to main. All checks must pass.

## Do not

- Add dependencies without explicit approval
- Modify CI workflow without explicit approval
- Create files outside established layout
- Add compatibility shims or backwards-compat code
- Add catch-all environment variables (e.g. `ENV=production`, `MODE=dev`, `STAGE=staging`) that toggle multiple unrelated behaviors. Each behavior gets own explicit named config knob — boot validation rejects missing required values rather than branching on single mode flag.

## Architecture

Key design: agents are opaque capability units with uniform interface, orchestrator owns routing and HITL policy, OpenTelemetry observability non-negotiable, context engineering prioritized over prompt gymnastics. See `docs/architecture/design-principles.md`.

- Two-graph pipeline (`planning`, `execution`) plus `chat`. Triage is chat-only — pipeline has no entry-time short-circuit, so `monet run` and chat's `/plan` both invoke planning directly. Revise-with-feedback lives inside planning subgraph's HITL loop (`MAX_REVISIONS=3`). See `docs/architecture/graph-topology.md`.
- **Chat graph contract (protocol-based, not type-based)**: client and TUI never import from `orchestration/chat`. Any replacement graph must accept `{"messages": [{role, content}]}` input and emit state patches with a `messages` field. `orchestration/chat/` is monet's default implementation — self-hosters replace the whole graph via `[chat] graph = "mymod:factory"` in `monet.toml`. Full contract and guide: `docs/guides/custom-graphs.md#replacing-the-chat-graph`.
- **Pointer-only orchestration, flat DAG execution** (post-commit `a176030`): planner writes full `work_brief` to artifact store, emits `work_brief_pointer` plus inline `RoutingSkeleton` (`{goal, nodes}`). `RoutingNode` carries `{id, agent_id, command, depends_on}`. Execution traverses DAG via `completed_node_ids`. Agent-side `inject_plan_context` hook resolves pointer at invocation time.
- Agent SDK (`@agent` decorator, `AgentStream`, signals, exceptions): `docs/guides/agents.md`, `docs/api/core.md`.
- **Hooks subsystem** (`core/hooks.py`, `hooks/`): worker hooks via `@on_hook("before_agent" | "after_agent")` run in worker process. Graph hooks via `GraphHookRegistry` run in server process at declared points. See `examples/custom-graph`.
- Orchestration (invoke_agent, task queue, pointer-only state, signal routing): `docs/guides/orchestration.md`, `docs/api/orchestration.md`.
- Distribution (pools, workers, monet.toml, CLI): `docs/guides/distribution.md`, `docs/api/server.md`.
- Artifact store: `docs/guides/artifacts.md`, `docs/api/artifacts.md`.
- Observability (OTel, Langfuse, trace continuity): `docs/guides/observability.md`.
- **Split-plane architecture**: `events/` has zero imports from any other monet package — `ProgressEvent`, `EventType`, `ClaimedTask`, `TaskRecord`, `TaskStatus` live there as wire-format data shapes. `queue/` owns transport protocols (`TaskQueue`, `QueueMaintenance`, `ProgressStore`) + backends. `worker/` owns claim loop (`run_worker`) and `DispatchBackend` protocol + push providers (ECS, Cloud Run, local subprocess). `progress/` owns `ProgressWriter` / `ProgressReader` protocols + SQLite and Postgres backends. Server exposes three named constructors: `create_unified_app` (S1–S3), `create_control_app` (control plane only), `create_data_app` (data plane only). `server/_event_router.py` classifies each event into `EventPolicy`: `DUAL_ROUTED` (domain events, stored + streamed), `EPHEMERAL_UI` (stream-only), `SILENT_AUDIT` (store-only). `TaskQueue` protocol extended to 9 methods: adds `renew_lease` (heartbeat) and `cancel` (abort). `DispatchBackend` protocol in `worker/_dispatch.py` — `submit(task, server_url, api_key)` for outbound-only ECS / Cloud Run dispatch; no inbound ports on workers. Pool config carries optional `dispatch` field; absent = in-process execution. `MonetClient` has dual-view interface: control-plane methods (`run`, `resume`, `abort`, `list_runs`) and data-plane methods (`subscribe_events`, `query_events`, `list_artifacts`). `data_plane_url` in `[planes]` config section; absent = both views hit unified URL. `PlanesConfig` / `ProgressConfig` / `ProgressBackend` in `config/_schema.py`. SSE stream emits `id: <event_id>` for `Last-Event-ID` reconnect.
- Server: Aegra (Apache 2.0 LangGraph Platform replacement). `monet dev` shells to `aegra dev`, production uses `aegra serve`. Worker/task routes mounted as Aegra custom HTTP routes via `_aegra_routes.py`. Server routes split by plane: `server/routes/_tasks_control.py` (claim/complete/fail), `server/routes/_tasks_data.py` (event record/query/stream).
- **CLI surface**: `monet dev` (group: default=start, `monet dev down` for teardown), `monet run` (default pipeline vs single-graph via `--graph <entrypoint>`), `monet runs` (list/inspect/pending/resume), `monet chat` (Textual TUI — HITL interrupts render as transcript text, next user submission resumes run), `monet worker` (registration is first heartbeat), `monet server [--plane unified|control|data]`, `monet status`.
- **Config-declared entrypoints**: `monet.toml [entrypoints.<name>]` with `graph = "<id>"`. Default: `default`, `chat`, `execution` invocable; `planning` internal. New invocable graph = config change, not code change.
- **Client / pipeline split**: `MonetClient` is graph-agnostic — `run(graph_id, input)` streams core events, `resume(run_id, tag, payload)` dispatches to paused interrupt, `abort(run_id)` terminates.

## Deployment scenarios

Six shapes. Full descriptions in `docs/architecture/deployment-scenarios.md`.

- **S1 local all-in-one** — `monet dev`, Docker-backed Postgres/Redis, `pool="local"` in-server.
- **S2 self-hosted production** — `aegra serve` + managed Postgres/Redis, `monet worker --server-url ...`, shared `MONET_API_KEY`.
- **S3 split fleet** — S2 with N worker pools via `monet.toml [pools]`. Pull pools (poll `claim()`) plus cloud dispatch pools (`dispatch = "ecs"` / `"cloudrun"`) — dispatcher claims, submits outbound to ECS/Cloud Run, claims next; no inbound ports.
- **S4 workers-only** — `monet worker` with no server URL, `InMemoryTaskQueue`. Test/library only.
- **S5 SaaS / split-plane** — vendor-hosted control plane (`create_control_app`), customer-hosted data plane (`create_data_app`). `MonetClient(url=control, data_plane_url=data)`. Customer telemetry and artifacts never leave customer infra. SaaS productization in separate downstream repo.
- **S6 embedded / no-server** — removed. Trigger to reintroduce: library-only use case.

## Standard ports and example lifecycle

Canonical local ports (defined in `src/monet/_ports.py`): Postgres `5432`, Redis `6379`, Dev server `2026`, Langfuse `3000`.

One example runs at a time. `monet dev` records active example's compose path in `~/.monet/state.json`, auto-tears-down previous example's containers before starting. On exit current example's containers torn down. Volumes preserved. `monet dev down` for explicit teardown.

## Aegra compatibility constraints

Aegra's graph loader supports filesystem paths only (not Python module paths), splits on `:` to separate file path from export name (breaks absolute Windows paths). `_langgraph_config.py:write_config()` resolves module paths to relative file paths before writing.

**Critical:** Aegra re-executes graph modules under synthetic `aegra_graphs.*` namespace — module body runs again in fresh namespace. `server_bootstrap.py` must never create process-singleton state (queues, global connections) at module body level. All such wiring lives in `bootstrap_server()`, called once from `_aegra_routes._lifespan`. Prevents split-brain `InMemoryTaskQueue`.

Aegra's factory classifier treats 1-arg function whose parameter isn't `ServerRuntime` as config-accepting factory. Real graph builders accept optional `hooks: GraphHookRegistry | None` kwarg, so `server_bootstrap.py` wraps them as 0-arg functions. Any new graph builder exported via `server_bootstrap.py` must also be wrapped as 0-arg.

Per-example `.monet/docker-compose.yml` files are pre-baked. Aegra's `is_postgres_running` check treats any container on port 5432 as "ours" — Phase 2 teardown in `src/monet/cli/_dev.py:_teardown_previous` prevents cross-example Postgres collisions. Future compose files should declare `container_name:`.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
