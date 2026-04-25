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

## Implicit Wire Formats

Systemic issue: many data shapes that cross process boundaries (HTTP, Redis, SSE) lack explicit wire-format schemas. Some boundaries use hand-built dicts with no TypedDict or Pydantic model; others have typed envelopes with untyped inner payloads.

**Pattern**: TypedDicts for LangGraph state boundaries, Pydantic models for REST wire formats with validation at deserialization.

**Gold standard** (already correct): `TaskRecord` (TypedDict + schema_version), `WorkBrief` / `RoutingSkeleton` (Pydantic round-trip), `RecordEventRequest` / `WorkerHeartbeatBody` (Pydantic request models).

### High severity

- **AgentResult** — dataclass hand-serialized via `serialize_result()` / `deserialize_result()` in `core/_serialization.py`. Crosses Redis (`result:{task_id}`), HTTP (`POST /tasks/{id}/complete`), and is nested inside schema-versioned `TaskRecord` without its own schema version. No shared type between worker client (`worker_client.py:199` hand-builds dict) and server (`TaskCompleteRequest` with `artifacts: list[dict[str, Any]]`).
- **Progress events** — Redis Streams `phist:*` uses `dict[str, Any]` with no schema. `POST /tasks/{id}/progress` accepts `body: dict[str, Any]`. Key name mismatch: agents emit `agent`, client types expect `agent_id`.
- **AgentStream protocol** — 5 event types (`progress`, `signal`, `artifact`, `result`, `error`) across subprocess/SSE/HTTP transports in `streams.py`. Protocol defined only in comments and runtime string checks.

### Medium severity

- **HTTP responses without models** — `GET /agents` returns hand-built dict with extra `worker_ids` field not in `Capability` model. `GET /deployments` returns `dict(r)`. Claim response (`POST /pools/{pool}/claim`) returns `dict(record)`, receiver does `resp.json()` with no validation.
- **Typed envelopes, untyped payloads** — `ProgressEvent.payload` is `dict[str, Any]`; HITL events rely on `payload.cause_id` by string convention. `TaskCompleteRequest.artifacts` and `.signals` are `list[dict[str, Any]]`.

### Low severity

- **Hook/webhook events** — bash hook stdin and webhook payloads are untyped `dict[str, Any]`. Extension points, harder to type fully.

### Proposed fix

Add Pydantic models for all REST wire formats (AgentResult wire shape, progress event envelope, AgentStream event union, response models for GET endpoints). Add schema_version to AgentResult wire format. Keep TypedDicts for LangGraph state boundaries.

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
- `monet.worker._dispatch.DispatchBackend` protocol
- `monet.events.ClaimedTask` TypedDict
- `monet.worker.push_providers.local.LocalDispatchBackend`
- `monet.worker.push_providers.ecs.ECSDispatchBackend`
- `monet.worker.push_providers.cloudrun.CloudRunDispatchBackend`
- `run_worker(dispatch_backend=..., server_url=..., api_key=...)` params

## Known-Dead: LocalDispatchBackend subprocess module

`monet.worker.push_providers.LocalDispatchBackend.submit()` spawns
`monet.worker.push_providers._dispatch_subprocess` as a subprocess entry point,
but that module does not exist. `LocalDispatchBackend` is dev/test only; the gap
has no user-visible impact unless someone explicitly configures a local dispatch
pool. Implement `_dispatch_subprocess` before shipping `LocalDispatchBackend` for
real use.
