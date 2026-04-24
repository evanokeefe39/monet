# Distribution Mode

monet supports distributed execution where the orchestration server and agent workers run as separate processes, potentially on different machines. This enables independent scaling, remote deployment, and multi-team agent development.

## Architecture overview

In distribution mode, three components collaborate:

1. **Orchestration server** — hosts graphs via Aegra, routes tasks to pools, manages worker lifecycle
2. **Workers** — poll for tasks by pool, execute agent handlers, report results
3. **Client** — submits work, streams events, handles HITL decisions

Workers register with the server via heartbeat. The server tracks capabilities in a manifest and dispatches tasks through a shared queue. When a worker disappears (missed heartbeats), its capabilities are removed and `invoke_agent` fails fast with `CAPABILITY_UNAVAILABLE`.

## Configuration: monet.toml

Pool topology is declared in `monet.toml` at the project root:

```toml
[pools.local]
type = "local"

[pools.default]
type = "pull"
lease_ttl = 600

[pools.cloud]
type = "push"
url = "https://cloud.example.com/tasks"
```

### Pool types

| Type | Description | Use case |
|---|---|---|
| `local` | In-process sidecar worker | Development, simple deployments |
| `pull` | Remote worker polls server for tasks | Standard distributed setup |
| `push` | Server forwards tasks to external endpoint | Cloud Run, ECS, Lambda |

### Environment variable resolution

Infrastructure values can be set via environment variables instead of hardcoding in the config:

```
MONET_POOL_{NAME}_URL  -> url
MONET_POOL_{NAME}_AUTH -> auth
```

For a pool named `remote`:

```bash
export MONET_POOL_REMOTE_URL=https://remote.example.com
export MONET_POOL_REMOTE_AUTH=secret-token
```

If no `monet.toml` exists, a single `local` pool is created by default.

## CLI commands

### monet server

Start the orchestration server.

```bash
monet server --host 0.0.0.0 --port 8000
monet server --config ./monet.toml --reload
monet server --plane control
monet server --plane data
```

| Option | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Host to bind to |
| `--port` | `8000` | Port to listen on |
| `--config` | `monet.toml` in cwd | Path to monet.toml |
| `--reload` | off | Enable auto-reload for development |
| `--plane` | `unified` | Plane to run: `unified`, `control`, or `data` |

The default (`--plane unified`) serves all routes in a single process. `--plane control` runs the control-plane only (worker management, task dispatch). `--plane data` runs the data-plane only (typed events, SSE, artifacts) and requires `MONET_PROGRESS_BACKEND`.

See [Server API Reference](../api/server.md) for endpoint details and the split-plane section below.

### monet worker

Start a standalone worker process.

```bash
monet worker --path ./agents --pool default --concurrency 10
monet worker --server-url http://orchestrator:8000 --api-key $MONET_API_KEY
```

| Option | Default | Description |
|---|---|---|
| `--path` | `.` | Directory to scan for agents |
| `--pool` | `local` | Pool to claim tasks from |
| `--concurrency` | `10` | Max concurrent task executions |
| `--server-url` | — | Orchestration server URL (env: `MONET_SERVER_URL`) |
| `--api-key` | — | API key for server auth (env: `MONET_API_KEY`) |

**Local mode** (no `--server-url`): Uses an in-memory queue in the same process. Useful for development.

**Remote mode** (with `--server-url`): Registers with the server, starts a heartbeat loop (30-second intervals), and claims tasks via HTTP. Capabilities are re-read each heartbeat cycle, enabling hot-reload when new agents are added to the scanned directory.

### monet dev

Start an Aegra dev server with monet's default graphs and worker/task routes.

```bash
monet dev
monet dev --port 2026 --config ./aegra.json
```

| Option | Default | Description |
|---|---|---|
| `--port` | `2026` | Port for the Aegra dev server |
| `--config` | — | Path to aegra.json (merged with monet defaults) |

If an `aegra.json` (or `langgraph.json`) exists in the current directory, its graphs are merged on top of monet's defaults (entry, planning, execution).

### monet run

Run a topic through the orchestration pipeline.

```bash
monet run "Research quantum computing trends"
monet run "Write a blog post about AI safety" --auto-approve
```

| Option | Default | Description |
|---|---|---|
| `--url` | `http://localhost:2026` | Aegra server URL (env: `MONET_SERVER_URL`) |
| `--auto-approve` | off | Auto-approve plans without prompting |

Connects to a running server, streams events to the terminal, and prompts for HITL decisions at plan approval and execution interrupt points.

**Interactive decisions:**

- **Plan approval**: approve, revise (with feedback), or reject
- **Execution interrupt**: retry or abort

### monet status

Show live workers and their capabilities.

```bash
monet status
monet status --pool default --flat
monet status --json
```

| Option | Default | Description |
|---|---|---|
| `--url` | `http://localhost:8000` | Orchestration server URL (env: `MONET_SERVER_URL`) |
| `--api-key` | — | API key for server auth (env: `MONET_API_KEY`) |
| `--pool` | — | Filter by pool name |
| `--flat` | off | Output as flat table (one row per capability) |
| `--json` | off | Output as JSON |

## Agent discovery

The CLI uses AST-based discovery to find `@agent`-decorated functions without executing any code. Both decorator forms are recognized:

```python
# Direct form
@agent("writer", command="draft", pool="default")
def write_draft(task: str) -> str: ...

# Partial form
writer = agent("writer")

@writer(command="deep")
def write_deep(task: str) -> str: ...
```

Discovery skips `__pycache__`, `.venv`, `node_modules`, dotfiles, and `test_*` files. Default values: `command="fast"`, `pool="local"`.

## Authentication

Server endpoints (except health) require a Bearer token:

```bash
export MONET_API_KEY=your-secret-key
```

Workers and CLI commands pass this via the `--api-key` flag or `MONET_API_KEY` environment variable. The server validates the token via middleware on all `/api/v1` endpoints except `/api/v1/health`.

## Worker lifecycle

1. **First heartbeat = registration** — Worker starts, discovers agents, POSTs `{pool, capabilities}` to `POST /api/v1/workers/{worker_id}/heartbeat`. The server upserts the worker's capability set in the `CapabilityIndex`; a new `worker_id` registers, a known one reconciles.
2. **Heartbeat loop** — Every 30 seconds, the worker POSTs the same endpoint with the current capability list. Capabilities are re-read each cycle (hot-reload).
3. **Task claiming** — Worker POSTs `POST /api/v1/pools/{pool}/claim` with `{consumer_id: worker_id, block_ms}`. The server rejects with 403 unless the worker is currently heartbeating for that pool (pool-scoped claim auth).
4. **Execution** — Worker executes agent handler, posts result via `POST /api/v1/tasks/{task_id}/complete` or `POST /api/v1/tasks/{task_id}/fail`.
5. **Stale cleanup** — Server runs a sweeper every 60 seconds. Workers with no heartbeat for 90 seconds are dropped from the `CapabilityIndex`; orphan capabilities (no remaining worker serving them) are pruned.

## Push pools and dispatch backends

Push pools forward claimed tasks to external compute via the `DispatchBackend` protocol. Three implementations ship with monet:

| Class | Target | Extra dependencies |
|---|---|---|
| `LocalDispatchBackend` | In-process (testing) | none |
| `CloudRunDispatchBackend` | Google Cloud Run jobs | `google-cloud-run` |
| `ECSDispatchBackend` | AWS ECS tasks | `aioboto3` |

The `submit(task, server_url, api_key)` call returns as soon as the job is dispatched. The spawned container calls `complete`/`fail` and renews the lease directly — the dispatch worker has no further responsibility.

## Split-plane deployment

For large deployments, the control and data planes can run on separate hosts:

```bash
# Control plane — worker registration, task dispatch
monet server --plane control --host 0.0.0.0 --port 8000

# Data plane — typed events, SSE streams, artifacts
MONET_PROGRESS_BACKEND=postgres \
monet server --plane data --host 0.0.0.0 --port 8001
```

Workers and clients set `MONET_DATA_PLANE_URL` to point event recording and queries at the data-plane host:

```bash
export MONET_SERVER_URL=http://control:8000
export MONET_DATA_PLANE_URL=http://data:8001
```

When `MONET_DATA_PLANE_URL` is unset, both planes are assumed to be at `MONET_SERVER_URL` (unified mode).

## Queue providers

The task queue is pluggable. Three implementations are available:

| Provider | Best for | Persistence | Dependencies |
|---|---|---|---|
| `InMemoryTaskQueue` | Development, testing | None | None |
| `SQLiteTaskQueue` | Single-server production | SQLite file | aiosqlite |
| `RedisTaskQueue` | Multi-server production | Redis | redis-py |

See [Queue Providers Reference](../api/queue.md) for constructor signatures and configuration.

## Environment variables

| Variable | Used by | Description |
|---|---|---|
| `MONET_API_KEY` | server, worker, CLI | Bearer token for authenticated endpoints |
| `MONET_SERVER_URL` | worker, CLI | Orchestration server URL |
| `MONET_DATA_PLANE_URL` | worker, client | Data-plane URL in split-plane deployments |
| `MONET_CONFIG_PATH` | server | Path to monet.toml |
| `MONET_ARTIFACTS_DIR` | server | Artifact Store storage directory |
| `MONET_AGENT_TIMEOUT` | orchestration | Task poll timeout in seconds (default 600) |
| `MONET_POOL_{NAME}_URL` | server | Pool endpoint URL |
| `MONET_POOL_{NAME}_AUTH` | server | Pool auth token |
| `MONET_PROGRESS_BACKEND` | server (data-plane) | `postgres` or `sqlite` (required for `--plane data`) |
| `MONET_PROGRESS_DB` | server (data-plane) | SQLite file path when `MONET_PROGRESS_BACKEND=sqlite` |
