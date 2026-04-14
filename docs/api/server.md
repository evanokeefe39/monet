# Server API Reference

## Python API

### `create_app`

```python
from monet.server import create_app

def create_app(
    config_path: Path | None = None,
    queue: TaskQueue | None = None,
) -> FastAPI
```

Creates a FastAPI application with worker management, task dispatch, and deployment tracking.

- Loads pool topology from `monet.toml` at `config_path` (defaults to cwd, falls back to single `local` pool)
- Creates `InMemoryTaskQueue` if no queue provided
- Initializes SQLite-backed deployment store
- Starts periodic stale-worker sweeper (60-second intervals, 90-second staleness threshold)
- Registers all routes under `/api/v1`

**App state** (available on `app.state`):

| Attribute | Type | Description |
|---|---|---|
| `queue` | `TaskQueue` | Task queue instance |
| `deployments` | `DeploymentStore` | Worker deployment tracking |
| `manifest` | `AgentManifest` | Capability manifest |
| `config` | `dict[str, PoolConfig]` | Pool topology |

### `bootstrap`

```python
from monet.server import bootstrap

async def bootstrap(
    *,
    artifacts_root: str | Path | None = None,
    enable_tracing: bool = True,
    agents: list[AgentCapability] | None = None,
    queue: TaskQueue | None = None,
    lazy_worker: bool = False,
) -> asyncio.Task[None] | None
```

One-call server initialization. Configures tracing, artifact store, manifest, queue, and worker in order.

| Parameter | Default | Description |
|---|---|---|
| `artifacts_root` | `None` | Artifact Store directory. Falls back to `MONET_ARTIFACTS_DIR`, then `.artifacts` |
| `enable_tracing` | `True` | Configure OpenTelemetry tracing |
| `agents` | `None` | Additional capabilities to declare in manifest |
| `queue` | `None` | Task queue. Defaults to `InMemoryTaskQueue` |
| `lazy_worker` | `False` | Defer worker startup to first enqueue |

Returns the worker `asyncio.Task` (cancel on shutdown), or `None` if `lazy_worker=True`.

### `configure_lazy_worker`

```python
from monet.server import configure_lazy_worker

def configure_lazy_worker(queue: TaskQueue) -> None
```

Patches `queue.enqueue()` to start the worker on first call. For `aegra dev` environments.

---

## HTTP endpoints

All routes are prefixed with `/api/v1`.

### Authentication

Endpoints except health require a Bearer token via the `Authorization` header:

```
Authorization: Bearer {MONET_API_KEY}
```

The API key is set via the `MONET_API_KEY` environment variable on the server.

---

### `GET /api/v1/health`

Returns server health status. **No authentication required.**

**Response:**
```json
{
    "status": "ok",
    "workers": 5,
    "queued": 12
}
```

| Field | Type | Description |
|---|---|---|
| `status` | `string` | Always `"ok"` |
| `workers` | `integer` | Active worker count |
| `queued` | `integer` | Pending task count |

---

### `POST /api/v1/worker/register`

Register a worker and its capabilities.

**Request body:**
```json
{
    "pool": "default",
    "capabilities": [
        {
            "agent_id": "researcher",
            "command": "deep",
            "description": "Deep research",
            "pool": "default"
        }
    ],
    "worker_id": "worker-abc-123"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `pool` | `string` | yes | Worker's pool |
| `capabilities` | `list` | yes | Agent capabilities |
| `worker_id` | `string` | yes | Unique worker identifier |

**Response:**
```json
{
    "deployment_id": "uuid-here"
}
```

---

### `POST /api/v1/worker/heartbeat`

Update worker heartbeat. Optionally reconcile capabilities.

**Request body:**
```json
{
    "worker_id": "worker-abc-123",
    "pool": "default",
    "capabilities": [...]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `worker_id` | `string` | yes | Worker identifier |
| `pool` | `string` | yes | Worker's pool |
| `capabilities` | `list` | no | If provided, reconciles manifest |

When `capabilities` is provided, the server:

1. Declares all listed capabilities for this worker
2. Removes any previously tracked capabilities no longer advertised by this worker
3. Updates the deployment record

Other workers' capabilities are untouched.

**Response:**
```json
{
    "status": "ok"
}
```

---

### `GET /api/v1/tasks/claim/{pool}`

Claim the next pending task from a pool.

**Path parameters:**

| Parameter | Type | Description |
|---|---|---|
| `pool` | `string` | Pool to claim from |

**Response (task available):** `TaskRecord` as JSON.

**Response (no tasks):** HTTP 204 No Content.

---

### `POST /api/v1/tasks/{task_id}/complete`

Post a successful result for a claimed task.

**Request body:**
```json
{
    "success": true,
    "output": "string or object or null",
    "artifacts": [
        {"artifact_id": "uuid", "url": "file:///..."}
    ],
    "signals": [
        {"type": "low_confidence", "reason": "Limited sources", "metadata": {}}
    ],
    "trace_id": "trace-abc",
    "run_id": "run-123"
}
```

**Response:**
```json
{
    "status": "ok"
}
```

---

### `POST /api/v1/tasks/{task_id}/fail`

Post a failure for a claimed task.

**Request body:**
```json
{
    "error": "Agent execution failed: timeout"
}
```

**Response:**
```json
{
    "status": "ok"
}
```

---

### `GET /api/v1/deployments`

List active deployments.

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `pool` | `string` (optional) | Filter by pool name |

**Response:**
```json
[
    {
        "deployment_id": "uuid",
        "pool": "default",
        "capabilities": [...],
        "worker_id": "worker-abc-123",
        "created_at": "2025-01-15T10:30:00Z",
        "last_heartbeat": "2025-01-15T10:31:30Z",
        "status": "active"
    }
]
```

---

### `POST /api/v1/deployments`

Create a deployment record. Used by `monet register` for CI/CD registration.

**Request body:**
```json
{
    "pool": "default",
    "capabilities": [
        {
            "agent_id": "researcher",
            "command": "deep",
            "description": "Deep research",
            "pool": "default"
        }
    ]
}
```

**Response** (HTTP 201):
```json
{
    "deployment_id": "uuid"
}
```
