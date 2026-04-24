# Issues

Known bugs, deprecations, standards violations, and design gaps. Check before picking maintenance work.

## E2E Test Gaps

No E2E coverage across deployment topologies. Needs tests for:

1. `monet dev` → `monet run` full pipeline with HITL approve/revise/reject
2. `aegra serve` with external Postgres
3. Multiple concurrent `monet worker` instances claiming from the same server
4. `RedisStreamsTaskQueue` under load against a real Redis
5. Custom graph registration via `aegra.json` with non-monet graphs driven via `--graph`
6. Worker reconnection after server restart
7. `monet run --auto-approve` happy path end-to-end

## Deferred Items

- **In-process (no-server) programmatic driver**: removed during client-decoupling refactor. Library callers use `monet dev` + `MonetClient`, or shell to `aegra dev`. Trigger: concrete need for server-less library usage (e.g. notebook example). If reintroduced, driver should use `build_default_graph` directly.

## Breaking Changes

### Push Worker Removed (Phase 0b)

`monet worker --push` and the inbound-HTTP push model have been removed.

**Why:** Push workers required an inbound HTTP port on the customer data plane, violating the data boundary for split-plane SaaS deployments.

**Migration:** Use the `DispatchBackend` protocol instead. Configure `dispatch = "ecs"` or `dispatch = "cloudrun"` in `monet.toml` pool config. The dispatch worker polls `claim()` outbound and submits jobs to ECS/Cloud Run — no inbound connectivity required.

Removed:
- `monet worker --push` CLI flag
- `monet.core.push_handler` module (`create_push_app`, `handle_dispatch`, `DispatchBody`)
- `monet.orchestration.push_with_retry`, `write_dispatch_failed`, `PUSH_MAX_ATTEMPTS`, `close_dispatch_client`
- `QueueMaintenance.record_push_dispatch`, `pop_push_dispatch`, `list_in_flight_push_dispatches`
- `PushDispatchTerminal` exception (use `DispatchBackend.submit` failure handling instead)

Added:
- `monet.queue._dispatch.DispatchBackend` protocol
- `monet.queue._dispatch.ClaimedTask` TypedDict
- `monet.queue.backends.dispatch_local.LocalDispatchBackend`
- `monet.queue.backends.dispatch_ecs.ECSDispatchBackend`
- `monet.queue.backends.dispatch_cloudrun.CloudRunDispatchBackend`
- `run_worker(dispatch_backend=..., server_url=..., api_key=...)` params
