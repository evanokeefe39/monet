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
```

| Option | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Host to bind to |
| `--port` | `8000` | Port to listen on |
| `--config` | `monet.toml` in cwd | Path to monet.toml |
| `--reload` | off | Enable auto-reload for development |

The server creates a FastAPI application with worker registration, heartbeat, task dispatch, and deployment management endpoints. See [Server API Reference](../api/server.md) for endpoint details.

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

### monet register

Register agent capabilities with the server from CI/CD pipelines.

```bash
monet register --path ./agents --server-url http://orchestrator:8000 --api-key $MONET_API_KEY
```

| Option | Default | Description |
|---|---|---|
| `--path` | `.` | Directory to scan for agents |
| `--server-url` | — | Orchestration server URL (env: `MONET_SERVER_URL`, required) |
| `--api-key` | — | API key for server auth (env: `MONET_API_KEY`, required) |

Discovers agents via AST scanning and posts deployment records to the server, grouped by pool.

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

1. **Registration** — Worker starts, discovers agents, sends capabilities to server via `POST /api/v1/worker/register`
2. **Heartbeat** — Every 30 seconds, worker sends capabilities to `POST /api/v1/worker/heartbeat`. Capabilities are re-read each cycle (hot-reload).
3. **Task claiming** — Worker polls `GET /api/v1/tasks/claim/{pool}` for pending tasks
4. **Execution** — Worker executes agent handler, posts result via `POST /api/v1/tasks/{task_id}/complete` or `POST /api/v1/tasks/{task_id}/fail`
5. **Stale cleanup** — Server runs a sweeper every 60 seconds. Workers with no heartbeat for 90 seconds are marked inactive and their capabilities removed from the manifest.

## Queue providers

The task queue is pluggable. Four implementations are available:

| Provider | Best for | Persistence | Dependencies |
|---|---|---|---|
| `InMemoryTaskQueue` | Development, testing | None | None |
| `SQLiteTaskQueue` | Single-server production | SQLite file | aiosqlite |
| `RedisTaskQueue` | Multi-server production | Redis | redis-py |
| `UpstashTaskQueue` | Serverless (Lambda, Vercel) | Upstash Redis (HTTP) | upstash-redis |

See [Queue Providers Reference](../api/queue.md) for constructor signatures and configuration.

## Environment variables

| Variable | Used by | Description |
|---|---|---|
| `MONET_API_KEY` | server, worker, CLI | Bearer token for authenticated endpoints |
| `MONET_SERVER_URL` | worker, CLI | Orchestration server URL |
| `MONET_CONFIG_PATH` | server | Path to monet.toml |
| `MONET_ARTIFACTS_DIR` | server | Artifact Store storage directory |
| `MONET_AGENT_TIMEOUT` | orchestration | Task poll timeout in seconds (default 600) |
| `MONET_POOL_{NAME}_URL` | server | Pool endpoint URL |
| `MONET_POOL_{NAME}_AUTH` | server | Pool auth token |
