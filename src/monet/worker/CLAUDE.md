# monet.worker — Worker Execution

## Responsibility

Claim loop and cloud dispatch backends. Execution-side of the queue protocol.
Transport (TaskQueue) lives in `monet.queue`; wire shapes (ClaimedTask, TaskRecord) live in `monet.events`.

## Public surface

`run_worker(queue, registry, pool, dispatch_backend, ...)` — claim loop. Pull-based: polls `queue.claim()` by pool, executes via `registry.lookup()`, or forwards to `dispatch_backend.submit()` for cloud pools.

## Modules

| Module | Owns |
|--------|------|
| `_loop.py` | `run_worker()` — claim loop, bounded concurrency, per-task timeout, heartbeat |
| `_dispatch.py` | `DispatchBackend` protocol — `submit(task, server_url, api_key)` |
| `push_providers/ecs.py` | `ECSDispatchBackend` — Fargate task per claim |
| `push_providers/cloudrun.py` | `CloudRunDispatchBackend` — Cloud Run Job per claim |
| `push_providers/local.py` | `LocalDispatchBackend` — subprocess per claim (dev only) |

## Design invariants

1. Worker knows nothing about graph topology or agent logic.
2. `dispatch_backend` is optional. Absent = in-process execution via registry.
3. Heartbeat loop fires at `lease_ttl / 3` for backends implementing `QueueMaintenance`.
4. `run_worker` runs until its `asyncio.Task` is cancelled. On cancel, drains in-flight up to `shutdown_timeout`.

## Known issues

- `push_providers/_dispatch_subprocess.py` does not exist. `LocalDispatchBackend` references it as the subprocess entry point. Tracked in ISSUES.md.
