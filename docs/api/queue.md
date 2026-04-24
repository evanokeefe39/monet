# Queue Providers Reference

## `TaskQueue` protocol

```python
from monet.queue import TaskQueue

class TaskQueue(Protocol):
    async def enqueue(
        self,
        agent_id: str,
        command: str,
        ctx: AgentRunContext,
        pool: str = "local",
    ) -> str: ...

    async def poll_result(self, task_id: str, timeout: float) -> AgentResult: ...

    async def claim(self, pool: str) -> TaskRecord | None: ...

    async def complete(self, task_id: str, result: AgentResult) -> None: ...

    async def fail(self, task_id: str, error: str) -> None: ...

    async def cancel(self, task_id: str) -> None: ...
```

Two sides:

- **Producer** (orchestration): `enqueue` submits a task, `poll_result` blocks until completion or timeout
- **Consumer** (workers): `claim` grabs the next pending task in a pool, `complete`/`fail` post results, `cancel` aborts

Workers claim by pool name (Prefect model). Handler lookup is the worker's responsibility.

### Lease heartbeat

Backends that implement `QueueMaintenance` expose `renew_lease(task_id)`. The worker loop calls it every `lease_ttl_seconds / 3` seconds (minimum 5 seconds) while a task is executing, so the reclaim sweeper does not evict active tasks. The call is a no-op on unknown task IDs (task may have already completed).

## `TaskStatus`

```python
from monet.queue import TaskStatus

class TaskStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

## `TaskRecord`

```python
from monet.queue import TaskRecord

class TaskRecord(TypedDict):
    task_id: str
    agent_id: str
    command: str
    pool: str
    context: AgentRunContext
    status: TaskStatus
    result: AgentResult | None
    created_at: str       # ISO 8601
    claimed_at: str | None
    completed_at: str | None
```

---

## `InMemoryTaskQueue`

```python
from monet.queue import InMemoryTaskQueue

queue = InMemoryTaskQueue(max_pending=0)
```

In-process queue backed by `asyncio.Queue`. No persistence, no external dependencies.

| Parameter | Default | Description |
|---|---|---|
| `max_pending` | `0` | Max pending tasks across all pools. 0 = unlimited. |

**Behaviour:**

- O(1) claim via `asyncio.Queue.get_nowait()`
- FIFO within each pool
- `poll_result` blocks on `asyncio.Event`, cleans up state after consuming
- Raises `RuntimeError` if `max_pending` exceeded on enqueue

**Properties:**

- `pending_count: int` — number of PENDING tasks across all pools

**Best for:** development, testing, single-process deployments.

---

## `SQLiteTaskQueue`

```python
from monet.queue import SQLiteTaskQueue

queue = SQLiteTaskQueue(db_path="tasks.db", lease_ttl=300)
await queue.initialize()
```

Persistent queue backed by SQLite with lease-based claiming and automatic crash recovery.

| Parameter | Default | Description |
|---|---|---|
| `db_path` | `":memory:"` | Path to SQLite database file |
| `lease_ttl` | `300` | Seconds before claimed task's lease expires |

**Additional methods:**

- `await queue.initialize()` — create tables and start sweeper. Safe to call multiple times.
- `await queue.close()` — close database connection and stop sweeper.
- `queue.start_sweeper()` / `queue.stop_sweeper()` — manual sweeper control.

**Behaviour:**

- WAL mode for concurrent reads
- Atomic `UPDATE...RETURNING` for O(1) claiming
- Background sweeper checks every 30 seconds for expired leases and requeues them
- FIFO ordering by `created_at`
- In-process `asyncio.Event` notification for `poll_result`

**Best for:** single-server production deployments.

---

## `RedisTaskQueue`

```python
from monet.queue import RedisTaskQueue

queue = RedisTaskQueue(
    url="redis://localhost:6379",
    lease_ttl=300,
    prefix="monet",
    use_polling=False,
)
```

Distributed queue backed by Redis with pub/sub notifications and optional polling fallback.

| Parameter | Default | Description |
|---|---|---|
| `url` | `"redis://localhost:6379"` | Redis URL. Accepts `redis://`, `rediss://`, `https://` |
| `lease_ttl` | `300` | Seconds before claimed task's lease expires |
| `prefix` | `"monet"` | Key namespace (allows multiple queues in same Redis) |
| `use_polling` | `False` | Use polling instead of pub/sub |

**Additional methods:**

- `await queue.close()` — close Redis connection and stop sweeper.
- `queue.start_sweeper()` / `queue.stop_sweeper()` — manual sweeper control.

**Behaviour:**

- FIFO via `LPUSH` + `RPOP`
- `poll_result` uses pub/sub by default (instant notification) or polling at 0.5s intervals
- `https://` URLs are auto-converted to `rediss://` for TLS
- Background sweeper uses `SCAN` cursor to avoid blocking on large key sets
- Handles both bytes and string values for redis-py compatibility

**Best for:** multi-server production deployments, high throughput.

---

## Choosing a provider

| Scenario | Provider |
|---|---|
| Local development | `InMemoryTaskQueue` |
| Single server, need persistence | `SQLiteTaskQueue` |
| Multiple servers, standard Redis | `RedisTaskQueue` |

All providers implement the same `TaskQueue` protocol and are interchangeable.

---

## `DispatchBackend` protocol

```python
from monet.queue._dispatch import DispatchBackend, ClaimedTask

class DispatchBackend(Protocol):
    async def submit(
        self,
        task: ClaimedTask,
        server_url: str,
        api_key: str,
    ) -> None: ...
```

Used by push pools to forward claimed tasks to external compute. `submit` returns after dispatching — the submitted container calls `complete`/`fail` and renews the lease directly.

### `ClaimedTask`

```python
class ClaimedTask(TypedDict):
    task_id: str
    run_id: str
    thread_id: str
    agent_id: str
    command: str
    pool: str
```

### Implementations

| Class | Use case |
|---|---|
| `LocalDispatchBackend` | In-process dispatch (testing) |
| `CloudRunDispatchBackend` | Google Cloud Run jobs |
| `ECSDispatchBackend` | AWS ECS tasks (requires `aioboto3`) |

---

## Progress events

### `EventType`

```python
from monet.queue._progress import EventType

class EventType(StrEnum):
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    STATUS = "status"
    HITL_CAUSE = "hitl_cause"
    HITL_DECISION = "hitl_decision"
    RUN_COMPLETED = "run_completed"
    RUN_CANCELLED = "run_cancelled"
```

### `ProgressEvent`

```python
from monet.queue._progress import ProgressEvent

class ProgressEvent(TypedDict, total=False):
    # Required — always present
    event_id: int       # 0 before write; assigned by ProgressWriter.record()
    run_id: str
    task_id: str
    agent_id: str
    event_type: EventType
    timestamp_ms: int
    # Optional enrichment
    trace_id: str
    payload: dict[str, Any]
```

### `ProgressWriter` protocol

```python
class ProgressWriter(Protocol):
    async def record(self, run_id: str, event: ProgressEvent) -> int: ...
```

`record` appends the event and returns the assigned `event_id`. Monotonic within `run_id`.

### `ProgressReader` protocol

```python
class ProgressReader(Protocol):
    async def query(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[ProgressEvent]: ...

    def stream(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[ProgressEvent]: ...

    async def has_cause(self, run_id: str, cause_id: str) -> bool: ...

    async def has_decision(self, run_id: str, cause_id: str) -> bool: ...
```

`query` returns events with `event_id > after`. `stream` yields events as they arrive and terminates on `run_completed`/`run_cancelled`. `has_cause`/`has_decision` support HITL idempotency checks.

### Backends

#### `SqliteProgressBackend`

```python
from monet.queue.backends.sqlite_progress import SqliteProgressBackend

backend = SqliteProgressBackend(db_path=":memory:")
```

Implements both `ProgressWriter` and `ProgressReader`. Backed by a single persistent `aiosqlite` connection. Uses `":memory:"` by default; pass a file path for durability. Configured at boot by `MONET_PROGRESS_DB`.

#### `PostgresProgressBackend`

```python
from monet.queue.backends.postgres_progress import PostgresProgressBackend

backend = PostgresProgressBackend(dsn="postgresql://...")
```

Implements both `ProgressWriter` and `ProgressReader`. Backed by Postgres. DSN is required when `MONET_PROGRESS_BACKEND=postgres`.
