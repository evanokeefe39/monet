# CLAUDE.md

## Project

monet is a multi-agent orchestration SDK for Python. MIT licensed, solo maintainer. The SDK provides an `@agent` decorator with pool assignment, typed context injection, an artifact store, orchestration via LangGraph with a task queue, and a FastAPI server layer. Keep things minimal and clean.

Known issues (bugs, deprecations, standards violations, design gaps) live in `ISSUES.md`. Roadmap features live in `docs/architecture/roadmap.md`. Check `ISSUES.md` before picking maintenance work — do not duplicate or paper over listed issues without explicit scope.

## Commits
Always use the `caveman:caveman-commit` skill to generate commit message subject + body. Do not hand-write commit messages. Invoke via the Skill tool (`skill: "caveman:caveman-commit"`) before running `git commit`.

## Layout

See `docs/reference/codebase-layout.md` for full per-module descriptions.

Top-level dirs:
- `src/monet/` — package source (src layout): `config/`, `core/`, `cli/`, `client/`, `hooks/`, `queue/`, `orchestration/`, `server/`, `agents/`, `artifacts/`, `_migrations/`
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

SymDex MCP is installed and the repo is registered (`~/.symdex/monet.db`). Its tools are **deferred** — their schemas are not loaded by default. For ANY symbol lookup, callgraph trace, file outline, or semantic search against `src/monet/**`, you **MUST** first load schemas with `ToolSearch` using:

```
query: "select:mcp__symdex__search_symbols,mcp__symdex__semantic_search,mcp__symdex__get_callers,mcp__symdex__get_callees,mcp__symdex__get_file_outline,mcp__symdex__get_repo_outline,mcp__symdex__search_routes,mcp__symdex__get_index_status"
```

Then use the tools. Only fall back to Read/Grep/Glob on `src/` when symdex returns nothing. Full-file `Read` remains correct for non-code files (toml/md/json/yaml) and when complete file context is required. The `symdex-code-search` skill documents the full tool surface.

Tool map:

- `mcp__symdex__search_symbols` / `get_symbol` — functions, classes, methods by name with exact byte offsets
- `mcp__symdex__semantic_search` — code by intent, not exact name
- `mcp__symdex__get_callers` / `get_callees` — call graph trace
- `mcp__symdex__get_file_outline` / `get_repo_outline` — structure without reading full files
- `mcp__symdex__search_routes` — HTTP endpoints across the codebase
- `mcp__symdex__get_index_status` — freshness at session start; reindex via `index_repo` if stale (watcher off by default)

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

- Two-graph pipeline (`planning`, `execution`) plus `chat`. Triage is a chat-only concern — the pipeline has no entry-time short-circuit, so `monet run` and chat's `/plan` both invoke planning directly. Revise-with-feedback lives inside the planning subgraph's HITL loop (`MAX_REVISIONS=3`). See `docs/architecture/graph-topology.md`.
- **Pointer-only orchestration, flat DAG execution** (post-commit `a176030`): planner writes a full `work_brief` to the artifact store and emits a `work_brief_pointer` plus an inline `RoutingSkeleton` (`{goal, nodes}`). `RoutingNode` carries `{id, agent_id, command, depends_on}`. Execution traverses the DAG via `completed_node_ids`. Agent-side `inject_plan_context` hook resolves the pointer at invocation time.
- Agent SDK (`@agent` decorator, `AgentStream`, signals, exceptions): `docs/guides/agents.md`, `docs/api/core.md`.
- **Hooks subsystem** (`core/hooks.py`, `hooks/`): worker hooks via `@on_hook("before_agent" | "after_agent")` run in the worker process. Graph hooks via `GraphHookRegistry` run in the server process at declared points. See `examples/custom-graph`.
- Orchestration (invoke_agent, task queue, pointer-only state, signal routing): `docs/guides/orchestration.md`, `docs/api/orchestration.md`.
- Distribution (pools, workers, monet.toml, CLI): `docs/guides/distribution.md`, `docs/api/server.md`.
- Artifact store: `docs/guides/artifacts.md`, `docs/api/artifacts.md`.
- Observability (OTel, Langfuse, trace continuity): `docs/guides/observability.md`.
- Server: Aegra (Apache 2.0 LangGraph Platform replacement). `monet dev` shells to `aegra dev`, production uses `aegra serve`. Worker/task routes mounted as Aegra custom HTTP routes via `_aegra_routes.py`.
- **CLI surface**: `monet dev` (group: default=start, `monet dev down` for teardown), `monet run` (default pipeline vs single-graph via `--graph <entrypoint>`), `monet runs` (list/inspect/pending/resume), `monet chat` (Textual TUI — HITL interrupts render as transcript text, next user submission resumes the run), `monet worker` (registration is the first heartbeat), `monet server`, `monet status`.
- **Config-declared entrypoints**: `monet.toml [entrypoints.<name>]` with `graph = "<id>"`. Default: `default`, `chat`, `execution` invocable; `planning` internal. Adding a new invocable graph is a config change, not a code change.
- **Client / pipeline split**: `MonetClient` is graph-agnostic — `run(graph_id, input)` streams core events, `resume(run_id, tag, payload)` dispatches to a paused interrupt, `abort(run_id)` terminates.

## Deployment scenarios

Six shapes. Full descriptions in `docs/architecture/deployment-scenarios.md`.

- **S1 local all-in-one** — `monet dev`, Docker-backed Postgres/Redis, `pool="local"` in-server.
- **S2 self-hosted production** — `aegra serve` + managed Postgres/Redis, `monet worker --server-url ...`, shared `MONET_API_KEY`.
- **S3 split fleet** — S2 with N worker pools via `monet.toml [pools]`. Pull pools today; push pools via webhook.
- **S4 workers-only** — `monet worker` with no server URL, `InMemoryTaskQueue`. Test/library only.
- **S5 SaaS** — vendor-hosted orchestrator, customer-hosted workers. SaaS productization in a separate downstream repo.
- **S6 embedded / no-server** — removed. Trigger to reintroduce: library-only use case.

## Standard ports and example lifecycle

Canonical local ports (defined in `src/monet/_ports.py`): Postgres `5432`, Redis `6379`, Dev server `2026`, Langfuse `3000`.

Only one example runs at a time. `monet dev` records the active example's compose path in `~/.monet/state.json` and auto-tears-down the previous example's containers before starting. On exit the current example's containers are torn down. Volumes are preserved. `monet dev down` for explicit teardown.

## Aegra compatibility constraints

Aegra's graph loader only supports filesystem paths (not Python module paths) and splits on `:` to separate file path from export name (breaks absolute Windows paths). `_langgraph_config.py:write_config()` resolves module paths to relative file paths before writing.

**Critical:** Aegra re-executes graph modules under a synthetic `aegra_graphs.*` namespace — the module body runs again in a fresh namespace. `server_bootstrap.py` must never create process-singleton state (queues, global connections) at module body level. All such wiring lives in `bootstrap_server()`, called once from `_aegra_routes._lifespan`. This prevents split-brain `InMemoryTaskQueue`.

Aegra's factory classifier treats a 1-arg function whose parameter isn't `ServerRuntime` as a config-accepting factory. The real graph builders accept an optional `hooks: GraphHookRegistry | None` kwarg, so `server_bootstrap.py` wraps them as 0-arg functions. Any new graph builder exported via `server_bootstrap.py` must also be wrapped as 0-arg.

Per-example `.monet/docker-compose.yml` files are pre-baked. Aegra's `is_postgres_running` check treats any container on port 5432 as "ours" — the Phase 2 teardown in `src/monet/cli/_dev.py:_teardown_previous` prevents cross-example Postgres collisions. Future compose files should declare `container_name:`.
