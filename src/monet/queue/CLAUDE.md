# monet.queue — Subpackage Interface Contract

## Responsibility

Queue owns task dispatch and result delivery between orchestration (producer) and workers (consumer). It is a transport layer.

Queue does NOT own: agent execution logic, orchestration routing, graph state, artifact storage, HITL policy, cost metering, or observability beyond OTel spans in the worker.

## Public Protocol (7 methods)

```python
class TaskQueue(Protocol):
    async def enqueue(task: TaskRecord) -> str
    async def claim(pool: str, consumer_id: str, block_ms: int) -> TaskRecord | None
    async def complete(task_id: str, result: AgentResult) -> None
    async def fail(task_id: str, error: str) -> None
    async def publish_progress(task_id: str, event: dict[str, Any]) -> None
    def subscribe_progress(task_id: str) -> AsyncIterator[dict[str, Any]]
    async def await_completion(task_id: str, timeout: float) -> AgentResult
```

## Public Types

- `TaskRecord` — TypedDict. Has `schema_version` field (currently 1). Serialized via `monet.core._serialization`.
- `TaskStatus` — StrEnum: PENDING, CLAIMED, COMPLETED, FAILED.
- `AwaitAlreadyConsumedError` — raised when TTL-expired result re-accessed.
- `TASK_RECORD_SCHEMA_VERSION` — int constant, current wire format version.

## Public Functions

- `run_worker(queue, registry, pool, max_concurrency, poll_interval, shutdown_timeout, task_timeout, consumer_id)` — claim loop with bounded concurrency and per-task timeout.

## Backends

- `InMemoryTaskQueue` — tests, local dev, S4 workers-only. No persistence. TTL-based result retention with proactive pruning.
- `RedisStreamsTaskQueue` — production. Redis Streams + Pub/Sub. Lazy-imported (not in `__all__`). Requires `redis>=5.0`.

## Design Invariants

These are load-bearing and must not be violated:

1. **Minimal stable interface.** Protocol is 7 methods. New methods require strong justification and must not couple to a specific transport.
2. **Transport-neutral.** Protocol makes no assumptions about Redis, Kafka, SQS, or any specific broker. Implementations handle leases, deduplication, and crash recovery internally.
3. **Layered ignorance.** Queue knows nothing about agents, orchestration graphs, or workflow topology. It receives TaskRecords and delivers AgentResults.
4. **Pull-based dispatch.** Workers claim tasks on demand. The queue never pushes to workers (push dispatch is a server-level concern layered on top, tracked via Redis-specific recovery methods).
5. **Agents are opaque.** Queue treats handler output as blackbox AgentResult. No inspection, no validation, no routing based on content.
6. **Progress is best-effort.** publish_progress must NOT raise. Dropped events are acceptable. Subscribers tolerate gaps.
7. **Idempotent completion.** complete() and fail() are safe to call multiple times for the same task_id. First write wins.
8. **Schema-versioned payloads.** TaskRecord carries `schema_version`. Deserialization rejects payloads with version > supported. Missing version treated as v1.

## Redis-Specific Surface (Not on Protocol)

These methods exist only on `RedisStreamsTaskQueue` and are accessed via isinstance dispatch by server/orchestration code. They are NOT part of the transport-neutral contract.

| Method | Purpose | Called from |
|--------|---------|-------------|
| `record_push_dispatch()` | Track in-flight push tasks for restart recovery | orchestration/_invoke.py |
| `pop_push_dispatch()` | Remove dispatch record on completion | orchestration/_invoke.py, server/_aegra_routes.py |
| `list_in_flight_push_dispatches()` | Boot recovery scan | server/_aegra_routes.py |
| `reclaim_expired_internal()` | Sweeper reclaims dead PEL entries | server/_aegra_routes.py |
| `ping()` | Health check (socket only) | server/_routes.py |
| `close()` | Connection cleanup | server/__init__.py |
| `_lease_ttl` (attr) | Derive sweeper interval | server/__init__.py |

## Callers

| Package | What it uses | Pattern |
|---------|-------------|---------|
| `orchestration/_invoke.py` | enqueue, await_completion, complete, fail, publish_progress, push dispatch | Producer: builds TaskRecord, enqueues, awaits result |
| `server/_routes.py` | claim, complete, fail, publish_progress, ping | HTTP adapter: translates REST to protocol calls |
| `server/_aegra_routes.py` | run_worker, push recovery methods | Lifespan: spawns in-process worker, runs boot recovery |
| `server/__init__.py` | TaskQueue (type), close, _lease_ttl | App factory: injects queue, configures sweeper |
| `server/server_bootstrap.py` | InMemoryTaskQueue, TaskQueue | Instantiates queue based on config |
| `cli/_worker.py` | run_worker, InMemoryTaskQueue | Spawns worker process |
| `core/worker_client.py` | TaskRecord (type) | RemoteQueue adapter implementing protocol over HTTP |
| `core/_serialization.py` | TaskRecord, TaskStatus | Wire format serialization |
| `tests/conftest.py` | InMemoryTaskQueue, run_worker | Auto-fixture for all async tests |

## Known Issues (from 2026-04-20 architecture review)

- Progress events are untyped `dict[str, Any]`. No schema, no validation.
- `list_in_flight_push_dispatches()` does unbounded SCAN — no pagination.
- InMemoryTaskQueue `_pruned_ids` uses set.pop() which is arbitrary-order eviction.
- No cost/token metering at queue layer (deferred to application-level observability).
- `ping()` checks socket only, not operational readiness (stream creation, pubsub).
