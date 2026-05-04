# monet.worker — Worker Execution

## Responsibility

Pool-config-driven claim loop, execution backends, transport adapters, workload
composition functions, data plane gateway, and remote queue client.

## Public surface

- `run_worker(queue, registry, pool, pools, pool_configs, agent_configs, ...)` —
  pool-config-driven claim loop. Routes each claimed task to the correct execution
  path based on `pool.backend` and `pool.workload`.
- `WorkerClient` — HTTP client for server API (heartbeat, claim, complete, fail).
- `RemoteQueue` — `TaskQueue` adapter wrapping `WorkerClient` for remote workers.

## Modules

| Module | Owns |
|--------|------|
| `_loop.py` | `run_worker()` — round-robin multi-pool claim loop, pool-config routing, semaphore, graceful shutdown. |
| `_client.py` | `WorkerClient` + `RemoteQueue` — HTTP transport for remote workers. |
| `_retry.py` | `retry_with_backoff()` — async retry with exponential backoff and jitter. |
| `transport/` | `TransportAdapter` + `Session` protocols; `HTTPTransport`, `CLITransport`, `SSETransport`. |
| `execution/` | `ExecutionBackend` protocol; `SubprocessBackend`, `DockerBackend`, `CloudRunBackend`, `ECSBackend`. |
| `workload/` | `execute_managed_workload`, `execute_persistent_workload`, `execute_cloud_push_workload`; `TaskRouter`, `ContainerSupervisor`. |
| `gateway/` | Embedded data plane gateway (FastAPI); JWT mint/validate; artifact/progress/signal routes. |

## Execution paths

| Pool backend | Workload | Function |
|---|---|---|
| `in_process` | — | `execute_task()` (in-process registry) |
| `cloudrun`, `ecs` | — | `execute_cloud_push_workload()` (fire-and-forget + poll) |
| `subprocess`, `docker`, `kubernetes` | `task` | `execute_managed_workload()` (per-task lifecycle) |
| `subprocess`, `docker`, `kubernetes` | `persistent` | `execute_persistent_workload()` (pooled instance) |

## Design invariants

1. Worker knows nothing about graph topology. Handler invocation delegates to `monet.core.engine.execute_task`.
2. External backends (non-in_process) manage their own lease renewal via `_run_with_lease`.
3. `TaskRouter` tracks idle/busy state for persistent pools; `has_capacity()` drives back-pressure.
4. `ContainerSupervisor` manages warm pool startup and restart for subprocess/docker only.
5. Gateway URL and task-scoped JWT are injected as `MONET_GATEWAY_URL` / `MONET_TOKEN` env vars.
6. `run_worker` runs until its `asyncio.Task` is cancelled. On cancel, drains in-flight up to `shutdown_timeout`.
