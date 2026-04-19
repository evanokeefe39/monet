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
8. Push pool round trip with a live Cloud Run Service / Lambda Function URL

## Deferred Items

- **In-process (no-server) programmatic driver**: removed during client-decoupling refactor. Library callers use `monet dev` + `MonetClient`, or shell to `aegra dev`. Trigger: concrete need for server-less library usage (e.g. notebook example). If reintroduced, driver should use `build_default_graph` directly.
