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

### `POST /api/v1/workers/{worker_id}/heartbeat`

Unified registration + liveness ping. First call from a new `worker_id`
registers; subsequent calls reconcile the capability set. Replaces the
legacy `/worker/register` + `/worker/heartbeat` pair.

**Path parameters:**

| Parameter | Type | Description |
|---|---|---|
| `worker_id` | `string` | Caller-chosen unique worker identifier |

**Request body:**
```json
{
    "pool": "default",
    "capabilities": [
        {
            "agent_id": "researcher",
            "command": "deep",
            "pool": "default",
            "description": "Deep research"
        }
    ]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `pool` | `string` | yes | Worker's pool |
| `capabilities` | `list[Capability]` | yes | Current capability set. Pydantic-validated: each field non-empty, `agent_id`/`command`/`pool` match `[a-z0-9_-]+` up to 64 chars; `description` up to 512 chars. |

**Response:**
```json
{
    "worker_id": "worker-abc-123",
    "known_capabilities": 3,
    "registered": true
}
```

`registered` is `true` only on the first call for that `worker_id`.

---

### `POST /api/v1/pools/{pool}/claim`

Claim the next pending task from a pool. Authenticated worker must be
currently heartbeating for `pool`; otherwise the server returns 403.

**Request body:**
```json
{"consumer_id": "worker-abc-123", "block_ms": 5000}
```

**Response (task available):** `TaskRecord` as JSON.

**Response (no tasks):** HTTP 204 No Content.

**Response (cross-pool claim):** HTTP 403.

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

