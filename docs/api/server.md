# Server API Reference

## Python API

### Named constructors

Three named constructors cover the three deployment shapes. `create_app` is the original legacy factory and remains available; prefer `create_unified_app` for new code.

#### `create_unified_app`

```python
from monet.server import create_unified_app

def create_unified_app(
    queue: TaskQueue,
    capability_index: CapabilityIndex,
    writer: ProgressWriter | None = None,
    reader: ProgressReader | None = None,
    artifact_store: ArtifactClient | None = None,
) -> FastAPI
```

Single-process app serving both control and data plane routes. Intended for S1/S2/S3 deployments. Data-plane routes (typed events, SSE, artifacts) are mounted but return 501 when `writer` and `reader` are not provided.

#### `create_control_app`

```python
from monet.server import create_control_app

def create_control_app(
    queue: TaskQueue,
    capability_index: CapabilityIndex,
) -> FastAPI
```

Control-plane-only app. Mounts: worker heartbeat, task claim/complete/fail, thread inspection, invocations, health. Accepts no `ProgressWriter` or `ArtifactStore` — the data boundary is enforced by the type system.

#### `create_data_app`

```python
from monet.server import create_data_app

def create_data_app(
    writer: ProgressWriter,
    reader: ProgressReader,
    artifact_store: ArtifactClient | None = None,
) -> FastAPI
```

Data-plane-only app. Mounts: typed event record/query, SSE stream, legacy progress endpoints, artifact CRUD, health. Accepts no `TaskQueue` or `CapabilityIndex`.

#### `create_app` (legacy)

```python
from monet.server import create_app

def create_app(
    config_path: Path | None = None,
    queue: TaskQueue | None = None,
) -> FastAPI
```

Original unified factory. Loads pool topology from `monet.toml` at `config_path` (defaults to cwd), creates `InMemoryTaskQueue` if no queue is provided, and registers all routes under `/api/v1`. Still functional; superseded by `create_unified_app` for explicit wiring.

### Internal 0-arg factories

`_create_control_plane()` and `_create_data_plane()` are Uvicorn `factory=True` entry points used by `monet server --plane control` and `monet server --plane data`. They read config from env/toml and are not intended to be called directly.

`_create_data_plane()` requires `MONET_PROGRESS_BACKEND` to be set; raises `ConfigError` if absent.

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

## Split-plane architecture

The server can run as a single unified process or as two separate processes on different hosts:

- **Control plane** — worker heartbeat, task claim/complete/fail, thread inspection, invocations. Started with `monet server --plane control`.
- **Data plane** — typed progress events, SSE streams, artifact CRUD. Started with `monet server --plane data`. Requires `MONET_PROGRESS_BACKEND`.
- **Unified** (default) — both planes in one process. `monet server` with no `--plane` flag.

Workers and clients set `MONET_DATA_PLANE_URL` to direct event recording and queries to the data-plane host when running in split mode.

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

### `POST /api/v1/runs/{run_id}/events`

Record a typed progress event for a run. Returns 501 when no `ProgressWriter` is configured.

**Request body:**
```json
{
    "task_id": "task-abc",
    "agent_id": "researcher",
    "event_type": "agent_started",
    "timestamp_ms": 1700000000000,
    "trace_id": "",
    "payload": {}
}
```

`event_type` must be a value from `EventType`: `agent_started`, `agent_completed`, `agent_failed`, `status`, `hitl_cause`, `hitl_decision`, `run_completed`, `run_cancelled`.

For `hitl_decision`: `payload.cause_id` is required; returns 400 if the referenced `hitl_cause` does not exist, and 409 if a decision for that `cause_id` is already recorded.

**Response (202):**
```json
{"event_id": 42}
```

---

### `GET /api/v1/runs/{run_id}/events`

Query typed progress events for a run. Returns 501 when no `ProgressReader` is configured.

**Query parameters:**

| Parameter | Default | Description |
|---|---|---|
| `after` | `0` | Return only events with `event_id > after` (cursor) |
| `limit` | `100` | Maximum events returned (1–1000) |

**Response:**
```json
{
    "run_id": "run-123",
    "events": [...],
    "count": 5
}
```

---

### `GET /api/v1/runs/{run_id}/events/stream`

Stream typed progress events as Server-Sent Events. Returns 501 when no `ProgressReader` is configured.

**Query parameters:**

| Parameter | Default | Description |
|---|---|---|
| `after` | `0` | Start after this `event_id` |

Each SSE message carries `id: <event_id>` so browser `EventSource` reconnects via the `Last-Event-ID` header automatically. The stream terminates when a `run_completed` or `run_cancelled` event is received.

To reconnect without duplicates, pass `?after=<last_event_id>`.

---

