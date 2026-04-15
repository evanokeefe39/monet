# ADR: Queue Backend Consolidation and Push-Pool Dispatch

**Status:** Proposed
**Date:** 2026-04-15
**Supersedes:** v2 of this file (2026-04-15 earlier)
**Frame:** Path to deploying first 100 production users without baking footguns that break at 1,000. Design for 100 today; refuse choices that paint into a corner before a 10× scale-out.

---

## Context

monet ships four `TaskQueue` backends today (`memory`, `sqlite`, `redis`, `upstash`) behind a stable protocol (`src/monet/queue/_interface.py`). The existing `RedisTaskQueue` uses Redis LISTs, has stub `publish_progress`/`subscribe_progress`, and was designed before Streams was on the table. The `UpstashTaskQueue` uses Upstash REST, which cannot do blocking commands or Pub/Sub subscribe — incompatible with the consumer-group model below.

Two production needs are unmet:

1. **Push-pool dispatch.** `monet.toml [pools.<name>] type = "push"` is declared in `src/monet/server/_config.py` but `src/monet/orchestration/_invoke.py:124` always enqueues regardless of pool type. Documented as `## Unimplemented` in CLAUDE.md.
2. **One coherent prod backend.** First 100 users need one well-tested path through the queue, not four partial ones.

This v3 narrows v2 in response to a multi-persona architecture review (16 high-severity findings against v2). The dropped pieces are listed under "Deferred" with a trigger each.

---

## Decision

1. **Ship one production backend: `RedisStreamsTaskQueue`** built on `redis-py` TCP. Replaces the LIST-based `RedisTaskQueue`. Operator picks Redis provider via `REDIS_URI` — Railway (recommended), Upstash TCP, ElastiCache, Memorystore, or self-hosted all speak native Redis protocol, no code change.
2. **Keep the `WorkQueue` protocol public.** Self-hosters with different operational requirements (existing Kafka, RabbitMQ, SQS) can implement it. monet ships and tests one impl; the protocol exists for users, not for hypothetical second backends inside this repo.
3. **Keep `InMemoryTaskQueue` in place** as the test fixture (autouse `_queue_worker` in `tests/conftest.py`). No move, no rename. Boot validation rejects it when `REDIS_URI` is set or `OperationConfig.queue_backend != "memory"` — no `ENV` flag (per CLAUDE.md `## Do not`).
4. **Delete `SqliteTaskQueue` and `UpstashTaskQueue` (REST).** S1 (local all-in-one) gets one more alpine container (~10MB RAM idle). Upstash remains a supported provider via TCP, not a separate backend.
5. **Push pool dispatch is provider-agnostic: HTTP POST to a configured webhook URL.** `invoke_agent` sees `pool.type == "push"` and POSTs `{task_id, token, payload, callback_url}` to `pool.url`. The user's cloud-side handler decodes and invokes the agent however their infrastructure dictates — Cloud Run Service with `min-instances=0`, AWS Lambda Function URL, Azure Container Apps HTTP trigger, or a thin user-written forwarder calling Cloud Run Jobs / ECS / batch APIs. monet ships zero cloud-provider SDKs as runtime deps. If/when convenience helpers are added (e.g. a `monet.providers.gcp.cloud_run_handler` decorator wrapping the user's FastAPI dispatch), they ship as optional installs (`monet[gcp]`, `monet[aws]`, `monet[azure]`) — never as required deps.
6. **Workers never connect to Redis directly.** Pull workers POST to Aegra HTTP endpoints; Aegra holds the Redis credentials. Push workers (Cloud Run containers) likewise call back over HTTPS, never touching Redis. Closes credential-distribution and rotation problems.
7. **Worker auth uses `MONET_API_KEY` for pull, HMAC-derived per-task token for push.** No new signing-key env var, no rotation problem. Push tokens are `HMAC(MONET_API_KEY, task_id)` — server recomputes to verify. Rotating `MONET_API_KEY` rotates all tokens automatically. JWT machinery deferred until evidence shows shared-key isolation is insufficient.
8. **Update the `WorkQueue` protocol** to a minimal Streams-shaped surface: `enqueue`, `claim(block_ms)`, `complete` (atomic ack+result), `fail`, `publish_progress`, `subscribe_progress`. Drop `wait_completion` (in-process helper, not queue concern), `acknowledge` (collapse into `complete`), `reclaim_expired` (Redis Streams uses `XPENDING`/`XCLAIM` directly, not exposed via protocol).

---

## Architecture

### Network topology

```
                  PUBLIC                          PRIVATE NETWORK
─────────────────────────────────────────────  ────────────────────────
Client ──SSE/HTTP──> Aegra (orchestrator)      ┌─ Redis (Streams + Pub/Sub)
                       │                       │
Pull workers ──HTTPS──> Aegra                  └─ Postgres (Aegra state)
(self-hosted)            │
                         │
Aegra ──HTTPS──> Provider Job API (Cloud Run, ECS, Lambda)
   │                          │
   └── (push workers callback over HTTPS) ────┘
```

- Redis is private. Only Aegra connects.
- Pull workers: outbound HTTPS to Aegra with `MONET_API_KEY`.
- Push workers: outbound HTTPS to Aegra with HMAC-derived per-task token.

### Worker → Aegra HTTP contract

Two endpoints, both already scaffolded in `src/monet/server/_routes.py`:

- `POST /api/v1/tasks/{task_id}/progress` — fire-and-forget. Returns `202 Accepted`. Aegra publishes to Redis Pub/Sub channel `progress:{task_id}` and returns immediately. Workers retry on 5xx with capped backoff (3 attempts, 100ms / 500ms / 2s); 4xx is dropped.
- `POST /api/v1/tasks/{task_id}/complete` — durable. Returns `200 OK` only after the result is durably stored. Idempotent on `task_id`. Workers retry on non-2xx until the task lease expires.

Pull workers also call `POST /api/v1/pools/{pool}/claim` to receive one task envelope or `204 No Content`. Aegra issues `XREADGROUP ... BLOCK 5000` server-side. The HTTP round trip is the cost of credential isolation.

### Auth

| Path | Credential | Verification |
|---|---|---|
| Client → Aegra | `MONET_API_KEY` bearer | Equality check (existing) |
| Pull worker → Aegra | `MONET_API_KEY` bearer | Equality check (existing) |
| Push worker → Aegra | `HMAC_SHA256(MONET_API_KEY, task_id)` bearer | Recompute server-side, compare |

Push tokens are bound to `task_id` so a compromised cloud container cannot replay across tasks. No `kid`, no `exp`, no JWT — derivation IS the rotation story (rotate `MONET_API_KEY` and every push token becomes invalid simultaneously, which is the desired blast radius).

### Stream topology

| Key shape | Type | Purpose | Retention |
|---|---|---|---|
| `work:{pool}` | Stream | Dispatch queue for pull pools, one consumer group per pool | `MAXLEN` configurable via `QueueConfig.work_stream_maxlen`, default deferred to first prod measurement |
| `result:{task_id}` | String with TTL | Completion result, key TTL = `agent_timeout * 2`, written by `/complete` route | TTL-bound |
| `progress:{task_id}` | Pub/Sub channel | Ephemeral progress relay during a task's lifetime | None — Pub/Sub is fire-and-forget by design |

Tenant scoping (`work:{tenant}:{pool}`) is **not** introduced now — it is owned by Priority 1 (pluggable auth + tenant context). Adding the `{tenant}` segment is a one-line key-shape change when that ADR lands.

No separate `completions` stream and no separate `lease:{task_id}` key:
- Completions are stored as `result:{task_id}` strings with TTL. `wait_completion` polls or subscribes to a per-task notification channel; on POST `/complete`, Aegra writes the string AND publishes to `result-ready:{task_id}`. No second stream consumer to manage, no PEL backlog, no MAXLEN-drops-completion hazard.
- Lease info is recovered from `XPENDING ... IDLE` directly. No parallel string key.

### Push dispatch (HTTP webhook, provider-agnostic)

`invoke_agent` checks pool type before touching the queue:

```python
async def invoke_agent(agent_id, command, ctx, pool="local"):
    pool_cfg = get_pool_config(pool)
    task_id = new_task_id()
    record_dispatch(task_id, pool, agent_id, command, ctx)  # writes to Redis hash for tracking

    if pool_cfg.type == "push":
        token = hmac_sha256(MONET_API_KEY, task_id)
        await httpx_client.post(
            pool_cfg.url,
            json={
                "task_id": task_id,
                "token": token,
                "callback_url": f"{settings.public_api_url}/api/v1/tasks/{task_id}",
                "payload": serialize_record(...),
            },
            headers={"Authorization": f"Bearer {pool_cfg.dispatch_secret}"},
            timeout=10.0,
        )
    else:
        await queue.enqueue(pool, task_record(task_id, ...))

    return await wait_completion(task_id, timeout=agent_timeout)
```

The user's webhook handler receives the POST, runs the agent, and POSTs back to `callback_url + /complete` (and `/progress` during execution) using the per-task HMAC token. monet sees one shape — an HTTP endpoint — regardless of which cloud the user runs.

Provider mapping examples:
- **Cloud Run Service** (`min-instances=0`): point `pool.url` at the service URL. Request triggers cold-start, handler runs agent, scales back down. Native fit.
- **AWS Lambda Function URL**: same pattern, Lambda invocation per request.
- **Azure Container Apps HTTP trigger**: same.
- **Cloud Run Jobs / ECS Fargate / batch APIs** (require IAM-authed Admin API): user writes a small forwarder service (~20 lines) that receives the webhook, calls the cloud Admin API to start a job, returns. monet stays out of the cloud SDK business.

`pool.dispatch_secret` is a separate bearer protecting the dispatch endpoint itself (so random internet traffic cannot trigger jobs). Configured in `monet.toml [pools.<name>]`.

Restart recovery: on Aegra restart, in-flight push tasks are recovered by inspecting the dispatch-tracking hash for tasks with `dispatched_at` set and no `result:{task_id}` key — these get reissued (idempotency guard: provider-side handler dedupes on `task_id`, since duplicate webhook delivery is the user's contract to handle).

### Pull dispatch

Pull workers run a claim loop against Aegra:

```
1. POST /api/v1/pools/{pool}/claim   → 200 with task envelope OR 204
   (Aegra issues XREADGROUP ... BLOCK 5000 server-side)
2. Execute agent handler.
3. Stream progress via POST /tasks/{task_id}/progress.
4. POST /tasks/{task_id}/complete with serialized result.
   (Aegra writes result:{task_id} string, publishes result-ready:{task_id}, XACKs the stream entry.)
```

### Progress flow

Inside `invoke_agent`, identical for pull and push:

```python
writer = get_stream_writer()  # LangGraph-scoped to current node

async def drain():
    async for event in queue.subscribe_progress(task_id):
        writer({"agent_id": agent_id, "task_id": task_id, "progress": event})

drain_task = asyncio.create_task(drain())
try:
    return await wait_completion(task_id, timeout=agent_timeout)
finally:
    drain_task.cancel()
```

`get_stream_writer()` is bound to the current node's coroutine by LangGraph. Aegra's existing SSE multiplexes graph state, LLM tokens, and these progress events to the client.

### Updated `WorkQueue` protocol

```python
class WorkQueue(Protocol):
    """Public interface. Reference impl: RedisStreamsTaskQueue.

    Self-hosters may implement against Kafka, RabbitMQ, SQS, etc.
    monet ships and tests only the Redis Streams impl.
    """

    async def enqueue(self, pool: str, task: TaskRecord) -> str: ...
    async def claim(self, pool: str, consumer_id: str, block_ms: int) -> TaskRecord | None: ...
    async def complete(self, task_id: str, result: AgentResult) -> None: ...
    async def fail(self, task_id: str, error: str) -> None: ...
    async def publish_progress(self, task_id: str, event: ProgressEvent) -> None: ...
    async def subscribe_progress(self, task_id: str) -> AsyncIterator[ProgressEvent]: ...
```

Six methods. No `wait_completion` (consumed by exactly one in-process caller, lives next to `invoke_agent`). No `acknowledge` (collapsed into `complete` — `XACK` happens inside `complete` after the result is durably written; if `complete` fails midway, lease expiry triggers redelivery via `XPENDING`/`XCLAIM` inside `RedisStreamsTaskQueue`, no protocol surface needed). No `reclaim_expired` (Redis-specific crash recovery, lives in the impl).

`enqueue` returns an opaque `str` task ID. The Streams impl uses the XADD entry ID as the durable identifier; other impls choose what fits their backend. Callers treat it as opaque.

### Message envelope

Stream entries and `result:{task_id}` strings carry a `serialize_result`-encoded `TaskRecord` / `AgentResult` (via the existing `src/monet/core/_serialization.py` helpers). Preserves typed round-trip for `tuple[Signal, ...]`, provenance fields (`agent_id`, `command`, `trace_id`, `run_id`, `created_at`), and `ArtifactPointer` for any large content.

`MAX_INLINE_PAYLOAD_BYTES = 950_000` (95% of Upstash's 1MB entry limit) constant in `src/monet/_ports.py`. `enqueue` enforces this — payloads above it must reference an `ArtifactPointer` or are rejected at the boundary. Test pins the limit.

No `schema_version` field today. Trigger to add: first incompatible envelope change.

---

## Alternatives Considered

### v2 ADR: forwarding worker for push, separate completions stream, signed JWT, tenant-scoped keys, `schema_version`

Reviewed by 5 architecture personas (16 high-severity findings). Rejected because:
- The forwarding-worker pattern reimplements provider-side scheduling (Cloud Run already has a job queue); direct API call is what Prefect does and it works.
- Separate `completions` stream creates single-writer Aegra ceiling, MAXLEN-drop hazard for completion events, and a `XACK`-after-resume race. `result:{task_id}` string with TTL is simpler and equally reliable.
- Signed JWT (`MONET_TASK_SIGNING_KEY`) introduces hand-rolled crypto with no rotation story, no `kid`, no `exp` policy. HMAC derivation from `MONET_API_KEY` provides the same per-task isolation with zero new env vars.
- `tenant=default` placeholder key shape is fake hook for an ADR (Priority 1) that owns tenant scoping.
- `schema_version: int` has no consumer; speculative versioning machinery.
- `MONET_ENV=production` gate violates CLAUDE.md `## Do not` (catch-all env var toggling unrelated behaviors).

### v1 ADR: parallel Pub/Sub progress + direct Cloud Run from graph nodes

Rejected for: duplicating monet's existing `publish_progress`/`subscribe_progress`; provider API calls inside graph nodes (graph-agnostic-server violation); shipping Redis credentials to containers; lossy `json.dumps` envelopes bypassing `serialize_result`; no migration story for LIST-based backend.

### Drop the `WorkQueue` protocol; ship a concrete class

Rejected after reviewer suggestion. Self-hosters with existing message infrastructure (Kafka, RabbitMQ, SQS) need a stable extension point. The protocol exists for them, not for hypothetical second backends inside this repo.

### Database polling (Prefect-style for orchestration state)

Functionally adequate at the target scale (100 users, IO-bound) but requires building polling logic, double-dispatch prevention, ack tracking, timeout-based failure detection as a bespoke queue on a database. Streams provides these as first-class operations; sqlite backend is being deleted along with the rest.

### Multiple shipped Redis providers (separate Upstash, ElastiCache, Memorystore impls)

Rejected. All three speak native Redis protocol over TCP. One `RedisStreamsTaskQueue` against `redis-py` works against all of them via `REDIS_URI`. Upstash REST SDK is dropped because it cannot do blocking commands or Pub/Sub subscribe.

### Bundling cloud-provider SDKs as runtime deps

Rejected. `google-cloud-run`, `boto3`, `azure-mgmt-containerinstance` together add ~50MB+ to the install footprint and lock monet to specific provider conventions. The HTTP webhook contract is provider-agnostic and zero-cost. If convenience helpers ship later (e.g. typed FastAPI handlers for common cloud-side patterns), they live as **optional installs**: `monet[gcp]`, `monet[aws]`, `monet[azure]`, `monet[all-providers]`. Default `pip install monet` brings no cloud SDKs.

---

## No-Footguns-at-1000 Audit

The frame is "100 users today, no choices that paint into a corner before 1,000." Every component in this ADR was reviewed against horizontal-scale failure modes. Specific guards:

- **Aegra horizontal scale.** Claim endpoint, completion writes, progress relay, and HMAC verification are all stateless per request. Multiple Aegra replicas behind a load balancer share the same Redis. The only single-writer concern (a future `completions`-stream consumer) was removed in favor of `result:{task_id}` strings, which any replica can write.
- **Stream sharding.** `work:{pool}` keyspace shape allows future hash-tagged sharding (`work:{pool:tenant}` or `work:{pool}:{shard_id}`) without refactoring the protocol — `claim` already takes `pool` as a parameter, callers can pass shard-suffixed pool names.
- **Tenant-scope hook.** Key shape stays `work:{pool}` today (no fake placeholder), but the segment is in the right position for `work:{tenant}:{pool}` to land cleanly when Priority 1 ships. Same for `result:`/`progress:` keys — `task_id` segment can absorb a `tenant:` prefix.
- **No numerical limits hardcoded.** `MAX_INLINE_PAYLOAD_BYTES` is the only constant; everything else (`MAXLEN`, `BLOCK_MS`, `agent_timeout`, sweeper interval, push-dispatch timeout) is a `QueueConfig` field. 10× scaling means tuning, not refactoring.
- **Webhook dispatch scales with provider.** Cloud Run / Lambda / ACA all autoscale per-request; monet's role is one HTTP POST per task, no batching, no provider-side coordination. Going from 100 → 1,000 concurrent push tasks is a config knob on the provider side.
- **Connection pooling.** `redis-py` async client is configured with `max_connections` from `QueueConfig.redis_pool_size`. Default sized for 100 concurrent claims; documented tuning guidance for higher.
- **Pub/Sub progress at scale.** Redis Pub/Sub is the one mechanism that does not horizontally scale cleanly past a single node. At 1,000 concurrent runs each emitting events, a single Redis instance's Pub/Sub fan-out stays under saturation for typical agent emit rates (a few events/sec). Trigger to revisit: measured Pub/Sub CPU > 50% on the Redis instance. Mitigation if hit: shard progress channels by `task_id` hash across Redis cluster nodes — same protocol, different deployment shape.

What is **not** guarded here, by design: multi-region, cross-cluster failover, cross-tenant isolation. Those land with their own ADRs when triggered.

---

## Provider Recommendation

| Stage | Provider | Approx cost | Notes |
|---|---|---|---|
| Dev | Docker Redis (compose) | $0 | Co-located with Postgres in `.monet/docker-compose.yml` |
| Prod (first 100 users) | Railway Redis with private networking | $5–10/mo | Usage-billed, no per-command fees, sub-ms latency to Aegra |

Other Redis-protocol-compatible providers (Upstash TCP, Memorystore, ElastiCache) work without code change. Recommended path for first-100-users scale is Railway because `monet.toml` already ships Railway examples and private networking eliminates the Pub/Sub egress cost concern.

---

## Migration Plan

Pre-prod, no users on the existing `RedisTaskQueue`/`SqliteTaskQueue`/`UpstashTaskQueue`. Clean cut, no compat shim.

1. **Update `WorkQueue` protocol** in `src/monet/queue/_interface.py` to the 6-method shape above. Mark public with docstring naming Kafka/RabbitMQ/SQS as known third-party impl targets.
2. **Implement `RedisStreamsTaskQueue`** in `src/monet/queue/backends/redis_streams.py`. Single `redis-py` async client. `XADD`/`XREADGROUP`/`XACK`/`XPENDING`/`XCLAIM` for dispatch. `PUBLISH`/`SUBSCRIBE` for progress. `SET ... EX` for `result:{task_id}`. `XADD MAXLEN` enforces stream cap.
3. **Update `InMemoryTaskQueue`** to satisfy the new 6-method protocol. Same import path. Used by `tests/conftest.py` autouse fixture.
4. **Delete** `src/monet/queue/backends/sqlite.py`, `src/monet/queue/backends/upstash.py`, `src/monet/queue/backends/redis.py` (the LIST impl). Drop `upstash-redis` from runtime deps.
5. **Add HMAC verification** in `src/monet/server/_auth.py` for push-worker callbacks. Recompute `HMAC_SHA256(MONET_API_KEY, task_id)` on inbound, compare. No new env var.
6. **Add push dispatcher** in `src/monet/orchestration/_invoke.py` — branch on `PoolConfig.type`, POST `{task_id, token, callback_url, payload}` to `pool.url` via `httpx`. No cloud-provider SDKs imported. Pool config (`url`, `dispatch_secret`, `timeout`) reads from `OperationConfig.pools.<name>` in `monet.toml`.
7. **Activate sweeper** in `src/monet/server/_bootstrap.py` lifespan — periodic call to `RedisStreamsTaskQueue.reclaim_expired_internal()` (impl-private, not on protocol). Closes the dormant-sweeper finding.
8. **Add `MAX_INLINE_PAYLOAD_BYTES` constant** in `src/monet/_ports.py` (rename from `_constants.py` per recent CLAUDE.md change). Enforce in `enqueue` and push-dispatcher payload construction.
9. **Update `/health`** in `src/monet/server/_routes.py` to `PING` Redis (and existing Postgres). Returns 503 on either failure.
10. **Update `QueueConfig.validate_for_boot()`** in `src/monet/config/_schema.py` — require `REDIS_URI` when `OperationConfig.queue_backend == "redis"`. Reject memory backend in any deploy that sets `REDIS_URI`. No `MONET_ENV` gate.
11. **Update `monet.toml` and `examples/deployed/`** to reference Railway Redis. Remove SQLite queue option from docs.
12. **Update CLAUDE.md**: remove push pool dispatch from `## Unimplemented`; reframe `[pools.<name>]` description with push behavior.

---

## Consequences

**Positive:**
- One queue impl to maintain, test, document. Public protocol still extensible for self-hosters.
- Workers never hold Redis credentials. Credential rotation is Aegra-only.
- Push dispatch closes `## Unimplemented` and works against any provider with a job-create API.
- Single SSE connection from client carries all event types.
- Provider choice is config (`REDIS_URI`), not code.
- Sweeper, push dispatcher, completion handling all become declared subsystems with owners — no dormant code.
- HMAC-per-task token requires no new env, no rotation procedure, no JWT library.
- `result:{task_id}` string with TTL is simpler than a `completions` stream — no PEL backlog, no XACK race, no MAXLEN-drop hazard for completion events.

**Negative:**
- Hard runtime dependency on Redis (in addition to existing Postgres). Adds one alpine container to `monet dev` (~10MB RAM idle, ~$5–10/mo on Railway in prod).
- Deletion of three backends is destructive; tests directly using `SqliteTaskQueue` or `UpstashTaskQueue` will break. Mitigation: review confirms only `_fakes.py` references `InMemoryTaskQueue`; no test imports the deleted backends.
- Push dispatcher is new code with its own failure modes (provider API throttling, restart recovery via dispatch-tracking hash). Needs explicit retry/backoff on the provider API call.
- Aegra worker thread per node during in-flight job. Acceptable for IO-bound async coroutines at 100-user scale; uvicorn worker sizing is a one-time tuning concern at first prod deploy, not an architecture problem.
- Claim endpoint adds one HTTP round trip per task. Acceptable for credential isolation; measured cost should be small at this scale.

**Neutral:**
- Upstash TCP works as a `REDIS_URI` target but `upstash-redis` REST SDK is dropped. Operators currently relying on REST-only network paths must switch to TCP.
- HMAC token rotation is coupled to `MONET_API_KEY` rotation. Rotating the API key invalidates all push tokens — that is the intended blast radius.

---

## Deferred (out of scope for this ADR)

Each item has a documented trigger. Pulled deliberately to keep this ADR minimal for the first-100-users path.

- **Tenant-scoped stream keys (`work:{tenant}:{pool}`).** Trigger: Priority 1 (pluggable auth + tenant context) lands.
- **Multi-replica Aegra completion handling.** Single-writer today (per-task `result:` strings). Trigger: second Aegra replica added — needs leader election or per-replica consumer-group split for the `claim` endpoint's `XREADGROUP`.
- **JWT-style signed tokens with `kid` + `exp`.** Trigger: HMAC-derived bearer proves insufficient (e.g. need cross-tenant token revocation without rotating `MONET_API_KEY`).
- **`schema_version` field on envelopes.** Trigger: first incompatible envelope change.
- **`MAXLEN` derivation from measured throughput.** Trigger: first prod measurement at 100 users — pick numbers from observed `XLEN`, not from round numbers.
- **`monet queue stats` / `monet queue reclaim` CLI inspectors.** Trigger: operator pages for a reclaim storm or completion backlog.
- **Per-tenant rate limiting on `/progress` and `/complete`.** Trigger: Priority 1 lands and brings tenant context to request handling.
- **Long-running job suspend pattern.** Currently node-stays-alive for all jobs. Trigger: measured worker thread pressure from jobs >5min wall time.
- **Push pool retry / circuit breaker on provider API failures.** Trigger: first observed transient throttling event (Cloud Run API has its own 429 semantics worth measuring before designing around).
- **Backup/restore for `result:{task_id}` strings or stream contents.** Trigger: a customer needs run replay across Redis primary failover. TTL-bound completions reduce this need for the first-100-users path.

---

## Open Questions

(Empty per CLAUDE.md spec gate. All known unknowns are listed above as Deferred with triggers.)
