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
    catalogue_root="/data/catalogue",
    enable_tracing=True,
    queue=my_queue,
)
```

```python
async def bootstrap(
    *,
    catalogue_root: str | Path | None = None,
    enable_tracing: bool = True,
    agents: list[AgentCapability] | None = None,
    queue: TaskQueue | None = None,
    lazy_worker: bool = False,
) -> asyncio.Task[None] | None
```

Initialization order:

1. **Tracing** -- configure OpenTelemetry (if `enable_tracing=True`)
2. **Catalogue** -- resolve root from parameter, `MONET_CATALOGUE_DIR` env, or `.catalogue` default
3. **Manifest** -- declare additional agent capabilities (if `agents` provided)
4. **Queue** -- register task queue (defaults to `InMemoryTaskQueue`)
5. **Worker** -- start background worker task, or defer to first enqueue if `lazy_worker=True`

Returns the worker task (cancel on shutdown) or `None` if `lazy_worker=True`.

## Lazy worker mode

For `langgraph dev` environments where the worker should not start until the first task:

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

### Worker registration

```
POST /api/v1/worker/register
```

Workers call this on startup with their discovered capabilities. Returns a `deployment_id`.

### Worker heartbeat

```
POST /api/v1/worker/heartbeat
```

Called every 30 seconds by workers. If capabilities are included, the server reconciles the manifest (adds new capabilities, removes stale ones for that worker).

### Task management

```
GET  /api/v1/tasks/claim/{pool}     # Claim next pending task (204 if empty)
POST /api/v1/tasks/{task_id}/complete  # Post successful result
POST /api/v1/tasks/{task_id}/fail      # Post failure
```

### Deployments

```
GET  /api/v1/deployments              # List active deployments (filter by ?pool=)
POST /api/v1/deployments              # Create deployment record (used by monet register)
```

See [Server API Reference](../api/server.md) for full request/response schemas.

## Deployment models

### Development

Use `monet dev` to start a LangGraph dev server with monet's default graphs:

```bash
monet dev --port 2024
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
