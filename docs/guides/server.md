# Server & Transport

monet includes a FastAPI-based orchestration server that manages agent dispatch, worker registration, and the task queue. Agents run as local Python functions or on remote workers -- the orchestrator handles routing transparently.

## Application factory

```python
from monet.server import create_app

app = create_app()
app = create_app(config_path=Path("monet.toml"))
```

`create_app()` builds a FastAPI application with:

- Pool topology from `monet.toml` (defaults to a single `local` pool)
- In-memory task queue (or pass a custom one)
- Deployment store for worker tracking
- Periodic stale-worker sweeper (60-second intervals)
- API routes under `/api/v1`

```python
def create_app(
    config_path: Path | None = None,
    queue: TaskQueue | None = None,
) -> FastAPI
```

| Parameter | Default | Description |
|---|---|---|
| `config_path` | `None` | Path to `monet.toml`. Falls back to cwd. |
| `queue` | `None` | Task queue instance. Defaults to `InMemoryTaskQueue`. |

## Bootstrap

For programmatic server setup (e.g. in tests or custom entrypoints), use `bootstrap()`:

```python
from monet.server import bootstrap

worker_task = await bootstrap(
    artifacts_root="/data/artifacts",
    enable_tracing=True,
    queue=my_queue,
)
```

```python
async def bootstrap(
    *,
    artifacts_root: str | Path | None = None,
    enable_tracing: bool = True,
    agents: list[AgentCapability] | None = None,
    queue: TaskQueue | None = None,
    lazy_worker: bool = False,
) -> asyncio.Task[None] | None
```

Initialization order:

1. **Tracing** -- configure OpenTelemetry (if `enable_tracing=True`)
2. **Artifact Store** -- resolve root from parameter, `MONET_ARTIFACTS_DIR` env, or `.artifacts` default
3. **Manifest** -- declare additional agent capabilities (if `agents` provided)
4. **Queue** -- register task queue (defaults to `InMemoryTaskQueue`)
5. **Worker** -- start background worker task, or defer to first enqueue if `lazy_worker=True`

Returns the worker task (cancel on shutdown) or `None` if `lazy_worker=True`.

## Lazy worker mode

For `aegra dev` environments where the worker should not start until the first task:

```python
from monet.server import configure_lazy_worker

configure_lazy_worker(queue)
```

This patches `queue.enqueue()` to start the worker on first call.

## Routes

All routes are prefixed with `/api/v1`. Authenticated endpoints require `Authorization: Bearer {MONET_API_KEY}`.

### Health (unauthenticated)

```
GET /api/v1/health
```

```json
{"status": "ok", "workers": 5, "queued": 12}
```

### Worker heartbeat (registration + liveness)

```
POST /api/v1/workers/{worker_id}/heartbeat
```

Single endpoint for both registration and liveness. First call from a new
`worker_id` registers; subsequent calls reconcile the capability set.
Body: `{pool, capabilities: [Capability]}`. Each `Capability` is a
pydantic-validated record with `agent_id`, `command`, `pool`, optional
`description`.

### Task management

```
POST /api/v1/pools/{pool}/claim        # Claim next pending task; body: {consumer_id, block_ms}
POST /api/v1/tasks/{task_id}/complete  # Post successful result
POST /api/v1/tasks/{task_id}/fail      # Post failure
```

`consumer_id` must be the worker's `worker_id`, and that worker must be
heartbeating for the named `pool`; otherwise the server returns 403.

### Deployments

```
GET /api/v1/deployments               # List active deployments (filter by ?pool=)
```

See [Server API Reference](../api/server.md) for full request/response schemas.

## Deployment models

### Development

Use `monet dev` to start an Aegra dev server with monet's default graphs:

```bash
monet dev --port 2026
```

### Single server

Start the orchestration server with a local worker:

```bash
monet server --port 8000
monet worker --pool local
```

### Distributed

Run the server and workers on separate machines:

```bash
# On orchestration server
monet server --port 8000 --config monet.toml

# On worker machines
monet worker --path ./agents --pool default \
  --server-url http://orchestrator:8000 \
  --api-key $MONET_API_KEY
```

See [Distribution Mode](distribution.md) for the full guide on distributed deployment, CLI commands, and configuration.
