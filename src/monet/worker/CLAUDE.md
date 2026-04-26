# monet.worker — Worker Execution

## Responsibility

Claim loop, cloud dispatch backends, and remote queue client. Execution-side of the queue protocol.
Transport (TaskQueue) lives in `monet.queue`; wire shapes (ClaimedTask, TaskRecord) live in `monet.events`.

## Public surface

- `run_worker(queue, registry, pool, dispatch_backend, ...)` — claim loop. Pull-based: polls `queue.claim()` by pool, executes via `registry.lookup()`, or forwards to `dispatch_backend.submit()` for cloud pools.
- `WorkerClient` — HTTP client for server API (heartbeat, claim, complete, fail).
- `RemoteQueue` — `TaskQueue` adapter wrapping `WorkerClient` so `run_worker()` works transparently against a remote server.

## Modules

| Module | Owns |
|--------|------|
| `_loop.py` | `run_worker()` — claim loop, bounded concurrency, semaphore, heartbeat, progress drain. `_execute()` delegates handler invocation to `monet.core.engine.execute_task`. |
| `_client.py` | `WorkerClient` + `RemoteQueue` — HTTP transport for remote workers. Implements consumer side of `TaskQueue` protocol over REST. |
| `_retry.py` | `retry_with_backoff()` — async retry with exponential backoff and jitter for HTTP calls. |
| `_dispatch.py` | `DispatchBackend` protocol — `submit(task, server_url, api_key)` |
| `push_providers/ecs.py` | `ECSDispatchBackend` — Fargate task per claim |
| `push_providers/cloudrun.py` | `CloudRunDispatchBackend` — Cloud Run Job per claim |
| `push_providers/local.py` | `LocalDispatchBackend` — subprocess per claim (dev only) |

## Design invariants

1. Worker knows nothing about graph topology or agent logic. Handler invocation lives in `monet.core.engine` — worker provides infrastructure (semaphore, heartbeat, progress drain) and delegates via `execute_task()`.
2. `dispatch_backend` is optional. Absent = in-process execution via registry.
3. Heartbeat loop fires at `lease_ttl / 3` for backends implementing `QueueMaintenance`.
4. `run_worker` runs until its `asyncio.Task` is cancelled. On cancel, drains in-flight up to `shutdown_timeout`.

## Known issues

- `push_providers/_dispatch_subprocess.py` does not exist. `LocalDispatchBackend` references it as the subprocess entry point. Tracked in ISSUES.md.
