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

## `UpstashTaskQueue`

```python
from monet.queue import UpstashTaskQueue

queue = UpstashTaskQueue(
    url="https://your-redis.upstash.io",
    token="your-token",
    prefix="monet",
    poll_interval=0.5,
    task_ttl=86400,
)
```

HTTP-based serverless queue backed by Upstash Redis. No persistent connections.

| Parameter | Default | Description |
|---|---|---|
| `url` | env `UPSTASH_REDIS_REST_URL` | Upstash Redis REST URL |
| `token` | env `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis REST token |
| `prefix` | `"monet"` | Key namespace |
| `poll_interval` | `0.5` | Seconds between poll_result status checks |
| `task_ttl` | `86400` | TTL for task keys in seconds (1 day default) |

**Behaviour:**

- Connectionless HTTP requests (ideal for serverless)
- `poll_result` always polls at intervals (no pub/sub)
- Redis TTL handles auto-cleanup of old tasks
- No internal lease sweeper — use external sweeper (e.g. QStash cron) for production
- Per-task hash: `{prefix}:task:{task_id}`, per-pool list: `{prefix}:queue:{pool}`

**Best for:** serverless deployments (Vercel, Lambda, Cloudflare Workers).

---

## Choosing a provider

| Scenario | Provider |
|---|---|
| Local development | `InMemoryTaskQueue` |
| Single server, need persistence | `SQLiteTaskQueue` |
| Multiple servers, standard Redis | `RedisTaskQueue` |
| Serverless / edge functions | `UpstashTaskQueue` |

All providers implement the same `TaskQueue` protocol and are interchangeable.
