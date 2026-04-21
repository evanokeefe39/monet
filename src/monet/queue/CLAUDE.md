# monet.queue — Interface Contract

## Responsibility

Transport layer. Task dispatch + result delivery between orchestration (producer) and workers (consumer). Owns nothing else: no agent logic, no graph state, no artifact storage, no HITL, no routing.

## Protocol (7 methods)

```python
class TaskQueue(Protocol):
    async def enqueue(task: TaskRecord) -> str
    async def claim(pool, consumer_id, block_ms) -> TaskRecord | None
    async def complete(task_id, result: AgentResult) -> None
    async def fail(task_id, error) -> None
    async def publish_progress(task_id, event: dict) -> None
    def subscribe_progress(task_id) -> AsyncIterator[dict]
    async def await_completion(task_id, timeout) -> AgentResult
```

## Public types

- `TaskRecord` — TypedDict, `schema_version` field (current: 1). Serialized via `monet.core._serialization`.
- `TaskStatus` — StrEnum: PENDING, CLAIMED, COMPLETED, FAILED.
- `AwaitAlreadyConsumedError` — TTL-expired result re-accessed.
- `TASK_RECORD_SCHEMA_VERSION` — int constant, current wire version.
- `ProgressStore` — `@runtime_checkable` Protocol. Methods: `get_progress_history(run_id, *, count)`, `expire_progress(run_id, ttl)`. Both backends implement it.

## Public functions

`run_worker(queue, registry, pool, max_concurrency, poll_interval, shutdown_timeout, task_timeout, consumer_id)` — claim loop, bounded concurrency, per-task timeout.

## Backends

- `InMemoryTaskQueue` — tests, local dev, S4 workers-only. TTL-based result retention with proactive pruning.
- `RedisStreamsTaskQueue` — production. Redis Streams + Pub/Sub. Lazy-imported (not in `__all__`). Requires `redis>=5.0`.

## Design invariants

1. Protocol is 7 methods. New methods need strong justification, must not couple to specific transport.
2. Protocol makes no assumptions about Redis/Kafka/SQS. Implementations handle leases, dedup, crash recovery internally.
3. Queue knows nothing about agents, graph topology, or workflow. Receives `TaskRecord`, delivers `AgentResult`.
4. Workers claim tasks on demand (pull). Queue never pushes to workers.
5. Queue treats handler output as blackbox `AgentResult`. No inspection, no routing on content.
6. `publish_progress` must NOT raise. Dropped events acceptable. Subscribers tolerate gaps.
7. `complete()` and `fail()` are idempotent. First write wins.
8. `TaskRecord` carries `schema_version`. Deserialization rejects `version > supported`. Missing version = v1.

## Redis key shapes

- `work:{pool}` stream — dispatch queue, one consumer group per pool
- `result:{task_id}` string — completion payload, TTL-bound
- `phist:{task_id}` stream — progress events (XADD/XRANGE), MAXLEN 1000, EXPIRE at completion (7d)
- `result-ready:{task_id}` pub/sub channel — completion notification
- `taskidx:{task_id}` hash — `{stream_id, pool}` for XACK

## Redis-only surface (not on Protocol)

| Method | Purpose | Caller |
|--------|---------|--------|
| `record_push_dispatch()` | Track in-flight push tasks | `orchestration/_invoke.py` |
| `pop_push_dispatch()` | Remove on completion | `orchestration/_invoke.py`, `server/_aegra_routes.py` |
| `list_in_flight_push_dispatches()` | Boot recovery scan | `server/_aegra_routes.py` |
| `reclaim_expired_internal()` | Sweeper reclaims dead PEL | `server/_aegra_routes.py` |
| `ping()` | Health check (socket only) | `server/_routes.py` |
| `close()` | Connection cleanup | `server/__init__.py` |
| `_lease_ttl` | Derive sweeper interval | `server/__init__.py` |

## Callers

| Package | Usage |
|---------|-------|
| `orchestration/_invoke.py` | enqueue, await_completion, complete, fail, publish_progress, push dispatch |
| `server/_routes.py` | claim, complete, fail, publish_progress, ping |
| `server/_aegra_routes.py` | run_worker, push recovery |
| `server/__init__.py` | TaskQueue type, close, _lease_ttl |
| `server/server_bootstrap.py` | InMemoryTaskQueue, TaskQueue instantiation |
| `cli/_worker.py` | run_worker, InMemoryTaskQueue |
| `core/worker_client.py` | TaskRecord type (RemoteQueue adapter) |
| `core/_serialization.py` | TaskRecord, TaskStatus wire format |
| `tests/conftest.py` | InMemoryTaskQueue, run_worker (auto-fixture) |

## Known issues

- Progress events untyped `dict[str, Any]`. Schema version `v: "1"` on stored entries but no payload validation.
- `list_in_flight_push_dispatches()` does unbounded SCAN — no pagination.
- `InMemoryTaskQueue._pruned_ids` uses `set.pop()` — arbitrary-order eviction.
- No cost/token metering at queue layer.
- `ping()` checks socket only, not operational readiness (streams, pubsub).
