# Worker Composition Model — Implementation Plan

Status: PLAN (pending review)
Date: 2026-05-04
Updated: 2026-05-04 (post-ADR-007, ADR-008 revisions)
Validated by: Spike rounds 1-4 (all pass, ~/repos/monet-arch-spike-webhooks-vs-a2a/)

---

## Summary

Refactor the worker from a binary dispatch path (in-process vs fire-and-forget) to
a pool-config-driven composition model where transport, execution backend, and
workload type are orthogonal axes. Transport lives on the agent declaration. Backend
and workload type live on the pool. The claim loop routes based on pool config.

AgentStream and its event handler subsystem are deprecated. A data plane gateway
handles all agent-to-platform communication (artifacts, progress, signals). Cloud-push
result delivery uses worker-managed polling, not webhooks. See ADR-007 and ADR-008.

---

## Decisions (locked in)

These were resolved in conversation and spike analysis. Not open for re-debate during
implementation unless new evidence surfaces.

- Transport type is an agent property (already on AgentEntryConfig)
- Backend type and workload type are pool properties
- A pool has one backend type; agents with different transports can share a pool
- Image/deployment is on the pool config, not the agent config
- Pools stay in monet.toml [pools.*] for now; runtime CRUD deferred to SaaS phase
- All backends go through one ExecutionBackend protocol; old DispatchBackend removed entirely
- Three transports built (HTTP, CLI, SSE); MCP stubbed with protocol only
- Two workload execution functions (managed + persistent) with shared helper; no WorkloadManager class/protocol
- K8s backend delegates supervision to K8s controllers; custom supervisor only for subprocess/docker
- Docker backend leverages native restart policy + HEALTHCHECK where available
- AgentStream deprecated and removed; .on()/.on_after() event handlers deprecated
- [[agent.on]] config section removed from agents.toml schema
- Cloud-push result delivery via worker polling, not webhooks (ADR-007)
- All agents communicate through data plane gateway, no worker localhost shortcut (ADR-008)
- Gateway embedded in worker for local dev, standalone for cross-network (ADR-008)
- Pool-scoped service configuration for backend store endpoints (ADR-008)
- Task-scoped JWT for agent authentication to gateway (ADR-008)

---

## What changes, what doesn't

### Unchanged

- `invoke_agent()` — enqueues task, awaits result, returns AgentResult
- `@agent` decorator — capability declaration and in-process registration
- `AgentResult` / `Signal` / `SignalType` — universal result contract
- `TaskQueue` protocol + Redis Streams backend
- `WorkerClient` — heartbeat, claim, complete (CP communication)
- `execute_task()` in engine.py — in-process agent execution
- LangGraph orchestration — planning, execution, signal router, HITL gates
- Progress, artifacts, schedule packages
- CLI, client SDK, server

### Removed

| File | Replacement |
|------|-------------|
| `worker/_dispatch.py` | `worker/execution/_protocol.py` |
| `worker/push_providers/__init__.py` | `worker/execution/__init__.py` |
| `worker/push_providers/local.py` | `worker/execution/_subprocess.py` |
| `worker/push_providers/cloudrun.py` | `worker/execution/_cloudrun.py` |
| `worker/push_providers/ecs.py` | `worker/execution/_ecs.py` |
| `streams.py` | Gateway + transport adapters |
| `handlers.py` | Gateway endpoints |
| `core/agent_loader.py` `[[agent.on]]` support | Removed (gateway replaces it) |

### New

| File | Purpose |
|------|---------|
| `worker/transport/_protocol.py` | TransportAdapter + Session protocols, ObservedEvent |
| `worker/transport/_errors.py` | TransportError, ProtocolError, AgentError |
| `worker/transport/_http.py` | HTTPTransport + HTTPSession |
| `worker/transport/_cli.py` | CLITransport + CLISession |
| `worker/transport/_sse.py` | SSETransport + SSESession |
| `worker/execution/_protocol.py` | ExecutionBackend protocol + Endpoint dataclass |
| `worker/execution/_subprocess.py` | SubprocessBackend |
| `worker/execution/_docker.py` | DockerBackend (from R4 spike) |
| `worker/execution/_cloudrun.py` | CloudRunBackend (migrated, conformed to protocol) |
| `worker/execution/_ecs.py` | ECSBackend (migrated, conformed to protocol) |
| `worker/workload/_managed.py` | execute_managed_workload() |
| `worker/workload/_persistent.py` | execute_persistent_workload() |
| `worker/workload/_collect.py` | _collect(), _run_with_lease() shared helper |
| `worker/workload/_router.py` | TaskRouter — idle/busy tracking, acquire/release |
| `worker/workload/_supervisor.py` | ContainerSupervisor — health, restart, circuit breaker, drain, orphans |
| `worker/gateway/__init__.py` | Gateway package |
| `worker/gateway/_app.py` | Gateway HTTP app (Starlette routes) |
| `worker/gateway/_auth.py` | JWT minting and validation |
| `worker/gateway/_routes.py` | Artifact, progress, signal endpoints |

---

## Target project structure

```
src/monet/worker/
    __init__.py              # MODIFIED exports
    _loop.py                 # REFACTORED — pool-config-driven routing
    _client.py               # UNCHANGED
    _retry.py                # UNCHANGED
    CLAUDE.md                # UPDATED
    transport/
        __init__.py
        _protocol.py         # TransportAdapter, Session, ObservedEvent
        _errors.py           # TransportError, ProtocolError, AgentError
        _http.py             # HTTPTransport, HTTPSession
        _cli.py              # CLITransport, CLISession
        _sse.py              # SSETransport, SSESession
    execution/
        __init__.py
        _protocol.py         # ExecutionBackend, Endpoint, ContainerSpec, JobStatus
        _subprocess.py       # SubprocessBackend
        _docker.py           # DockerBackend
        _cloudrun.py         # CloudRunBackend
        _ecs.py              # ECSBackend
    workload/
        __init__.py
        _managed.py          # execute_managed_workload()
        _persistent.py       # execute_persistent_workload()
        _collect.py          # _collect(), _run_with_lease()
        _router.py           # TaskRouter
        _supervisor.py       # ContainerSupervisor
    gateway/
        __init__.py
        _app.py              # Gateway Starlette app, embedded + standalone modes
        _auth.py             # JWT mint/validate, dev-mode constant key
        _routes.py           # /artifacts, /progress, /signals endpoints
```

---

## Protocols

### TransportAdapter + Session

```python
@runtime_checkable
class Session(Protocol):
    async def submit(self, payload: dict[str, Any]) -> None: ...
    def receive(self) -> AsyncIterator[ObservedEvent]: ...
    async def cancel(self) -> None: ...
    async def close(self) -> None: ...

@runtime_checkable
class TransportAdapter(Protocol):
    async def connect(self, endpoint: Endpoint) -> Session: ...
```

Validated in R3 (S2-S3, S5-S8).

### ExecutionBackend

```python
@runtime_checkable
class ExecutionBackend(Protocol):
    async def start(self, spec: ContainerSpec, env: dict[str, str]) -> Endpoint: ...
    async def poll_status(self, endpoint: Endpoint) -> JobStatus: ...
    async def stop(self, endpoint: Endpoint, grace_period_s: float) -> None: ...
    async def kill(self, endpoint: Endpoint) -> None: ...
```

Validated in R3 (SubprocessBackend) and R4 (DockerBackend).

`poll_status` returns `JobStatus` enum: `running`, `succeeded`, `failed`, `unknown`.

Subprocess and Docker backends implement `poll_status` via process/container
status checks. CloudRun uses `GetExecution`. ECS uses `DescribeTasks`.

Cloud backends (CloudRun, ECS) implement `start()` as fire-and-forget dispatch.
`stop()` and `kill()` are best-effort cancellation via cloud API.

### Endpoint

```python
@dataclass(frozen=True)
class Endpoint:
    address: str                           # "http://127.0.0.1:8080" or "cli://pid/1234"
    process_id: str                        # container ID, PID, or task ARN
    backend_type: str                      # "subprocess", "docker", "cloudrun", "ecs"
    metadata: dict[str, Any] = field(default_factory=dict)
```

### JobStatus

```python
class JobStatus(Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
```

### ObservedEvent

```python
@dataclass(frozen=True)
class ObservedEvent:
    type: str                              # "result", "transport_error", "transport_metric"
    data: dict[str, Any]
    timestamp: float                       # time.monotonic()
```

Transport adapters yield only result events and transport-level observations.
Progress, signals, and artifacts travel via the data plane gateway.

---

## Pool config schema (extended)

```toml
# monet.toml — existing [pools.*] section, extended

[pools.local]
backend = "in_process"

[pools.dev-subprocess]
backend = "subprocess"
workload = "task"
concurrency = 4
task_timeout_s = 300

[pools.docker-research]
backend = "docker"
workload = "persistent"
image = "registry.internal/openclaw:2.1"
concurrency = 2
task_timeout_s = 600
warm_pool_size = 2
startup_timeout_s = 30
graceful_shutdown_s = 30
heartbeat_interval_s = 10
restart_policy = "on_failure"
max_restarts = 3
restart_window_s = 300
backpressure_queue_max = 10

[pools.k8s-prod]
backend = "kubernetes"
workload = "persistent"
namespace = "monet-agents"
deployment = "openclaw"
concurrency = 8
task_timeout_s = 3600

[pools.cloud-burst]
backend = "cloudrun"
project = "my-project"
region = "us-central1"
job = "monet-worker"
task_timeout_s = 300
poll_interval_s = 5
gateway = "https://dp.example.com"   # gateway URL for agents in this pool
```

### Gateway configuration

```toml
# monet.toml — gateway section

[gateway]
port = 2027                              # default gateway port (dev mode)
signing_key_env = "MONET_GATEWAY_KEY"    # env var holding JWT signing key
# tunnel = "cloudflare"                  # optional: auto-start cloudflared
```

In `monet dev` mode, the gateway starts embedded in the worker process with a
known dev signing key. In production, deploy as standalone service.

### Agent config (simplified)

```toml
[[agent]]
id = "openclaw-researcher"
transport.type = "http"
pool = "docker-research"
command = "research"
description = "Deep research via OpenClaw"

[[agent]]
id = "pdf-extractor"
transport.type = "cli"
transport.cmd = ["python", "-m", "pdf_extract"]
pool = "dev-subprocess"
command = "extract"

[[agent]]
id = "email-classifier"
pool = "local"

[[agent]]
id = "burst-summarizer"
transport.type = "http"
pool = "cloud-burst"
```

### Migration from current pool types

| Current type | New backend | Notes |
|---|---|---|
| `local` | `in_process` | Rename only. Same behavior. |
| `pull` | `subprocess` or `docker` or `kubernetes` | Pull workers now specify their backend type. |
| `push` | `cloudrun` or `ecs` | Push pools now name the specific cloud backend. |

Boot validation: reject old type values (`local`, `pull`, `push`) with a clear
error message pointing to the new schema. No silent fallback.

---

## Data plane gateway

See ADR-008 for full rationale. Summary:

All agents communicate with monet's shared services (artifact store, progress
store, signals) through the gateway. No direct backend access from agents.
No worker localhost shortcut.

### Gateway API

```
POST /artifacts/{task_id}       — write artifact (multipart upload)
GET  /artifacts/{task_id}/{key} — read artifact
POST /progress/{task_id}        — emit progress event (JSON)
POST /signals/{task_id}         — emit signal (JSON)
GET  /health                    — liveness check
```

### Authentication

Task-scoped JWT. Worker mints token at dispatch time with claims:
`task_id`, `pool`, `scopes`, `exp`. Gateway validates and enforces
task-level isolation.

Dev mode: signing key is a known constant. No key management for
`monet dev`.

### Agent access patterns

| Agent type | How it calls gateway |
|---|---|
| Python agent | MCP sidecar tools (thin HTTP clients) |
| Coding agent (OpenClaw) | monet CLI as bash tool: `monet artifact write result.json` |
| Any runtime | Raw HTTP POST with `Authorization: Bearer {token}` |

All read `MONET_GATEWAY_URL` and `MONET_TOKEN` from env vars.

### Deployment modes

| Scenario | Gateway runs as | URL |
|---|---|---|
| `monet dev` | Embedded in worker | `http://localhost:2027` |
| Local + cloud push | Docker container + Cloudflare Tunnel | `https://{id}.trycloudflare.com` |
| Self-hosted prod | Standalone behind LB | `https://dp.example.com` |
| Managed DP (future) | Monet-hosted | `https://dp.monet.example` |

---

## Cloud-push result delivery (ADR-007)

No webhooks. Worker manages the full lifecycle:

```python
case "cloudrun" | "ecs":
    backend = _resolve_backend(pool)
    gateway_url = pool.gateway or default_gateway_url
    dp_env = {
        "MONET_GATEWAY_URL": gateway_url,
        "MONET_TOKEN": _mint_task_token(record, pool),
    }
    endpoint = await backend.start(
        ContainerSpec(image=pool.image, entrypoint=agent.transport.cmd),
        {**dp_env, **_task_env(record)},
    )
    # Poll for completion — no webhooks, no callbacks
    while True:
        status = await backend.poll_status(endpoint)
        if status == JobStatus.SUCCEEDED:
            result = await _retrieve_result(gateway_url, record.task_id, token)
            await queue.complete(record.task_id, result)
            break
        elif status == JobStatus.FAILED:
            await queue.fail(record.task_id, "cloud job failed")
            break
        await asyncio.sleep(pool.poll_interval_s)
```

Cloud-push agent writes its result to the gateway as an artifact. Worker
polls cloud API for job status, retrieves result from gateway on completion.

### Orphan recovery

If worker dies mid-poll, task lease expires, another worker reclaims.
Task record stores endpoint metadata (execution ID). New worker can
resume polling or let the cloud job time out.

---

## Supervision matrix — what each backend provides

| Feature | Subprocess | Docker | Kubernetes | CloudRun/ECS |
|---|---|---|---|---|
| Start/stop/kill | Custom | docker-py | K8s API | Cloud API |
| Liveness | `proc.returncode` | `container.status` | livenessProbe **native** | Cloud-managed |
| Readiness | Custom HTTP poll | Custom HTTP poll | readinessProbe **native** | Cloud-managed |
| Restart on failure | **Custom** | restart policy **native** | restartPolicy **native** | Retries **native** |
| Backoff | **Custom** | Docker backoff **native** | CrashLoopBackOff **native** | **Native** |
| Circuit breaker | **Custom** | `on-failure:N` **partial** | CrashLoopBackOff **partial** | Max retries |
| Warm pool | **Custom** | **Custom** | `replicas` **native** | `min-instances` **native** |
| Health monitoring | **Custom** | HEALTHCHECK **partial** | Probes **native** | **Native** |
| Graceful drain | **Custom** | **Custom** | preStop hook **native** | N/A |
| Orphan cleanup | OS reparent | Labels + kill **custom** | ownerReferences **native** | **Native** |
| Resource limits | ulimit/cgroups | `--memory/--cpus` **native** | `resources` **native** | Config **native** |
| Result delivery | Transport session | Transport session | Transport session | Worker polls cloud API |

### Supervision tiers

**Full custom (subprocess):** ContainerSupervisor manages everything. Dev/test only.

**Partial delegation (docker):** Docker handles restart policy and backoff natively.
ContainerSupervisor manages warm pool startup, task routing, circuit breaker (beyond
Docker's restart count), drain, and orphan reconciliation via labels.

**Full delegation (kubernetes):** K8s controllers handle restart, backoff, warm pool
(replicas), health probes, drain, orphans, resource limits. Monet manages only:
transport session pool (connect to pods, submit work, collect results) and task
routing (pick a ready pod). ContainerSupervisor is NOT used. TaskRouter is used.

**Poll-and-collect (cloudrun, ecs):** Cloud manages container lifecycle. Worker
dispatches and polls for completion (ADR-007). No supervision code runs. Gateway
handles in-flight agent communication.

---

## Phases

### Phase 0: Config schema

**What:** Extend PoolConfig and AgentEntryConfig. Add gateway config.

**Files changed:**
- `config/_pools.py` — new PoolConfig fields (backend, workload, lifecycle params,
  backend-specific fields, gateway URL, poll_interval_s). New `_VALID_BACKENDS`,
  `_VALID_WORKLOADS` sets. Boot validation: reject old type values, validate
  backend-specific required fields.
- `config/_schema/_worker.py` — WorkerConfig gains `pools: list[str]` (multi-pool
  support). Backwards compat: if `pool` is set and `pools` is not, `pools = [pool]`.
- `config/_schema.py` — GatewayConfig dataclass (port, signing_key_env, tunnel).
- `core/agent_loader.py` — remove `AgentEventHandlerConfig`, `[[agent.on]]` parsing.

**Done when:**
- [ ] `load_pool_config()` parses new schema from monet.toml
- [ ] Old pool types (`local`/`pull`/`push`) rejected with migration error message
- [ ] Backend-specific required fields validated at boot
- [ ] GatewayConfig parsed from `[gateway]` section
- [ ] `[[agent.on]]` removed from agent loader; old configs with `on` rejected
- [ ] Tests pass for config parsing, validation, and error messages

---

### Phase 1: Protocols and shared types

**What:** Define the protocol interfaces and shared types. No runtime behavior.

**Files created:**
- `worker/transport/__init__.py`
- `worker/transport/_protocol.py` — Session, TransportAdapter protocols; ObservedEvent
- `worker/transport/_errors.py` — TransportError, ProtocolError, AgentError
- `worker/execution/__init__.py`
- `worker/execution/_protocol.py` — ExecutionBackend protocol; Endpoint; ContainerSpec; JobStatus

**Done when:**
- [ ] Protocols are runtime-checkable
- [ ] All types have full type annotations
- [ ] JobStatus enum includes running, succeeded, failed, unknown
- [ ] mypy passes
- [ ] No runtime imports beyond stdlib + typing

---

### Phase 2: Transport adapters

**What:** Implement HTTP, CLI, SSE adapters. Stub MCP.

**Files created:**
- `worker/transport/_http.py` — HTTPTransport, HTTPSession
- `worker/transport/_cli.py` — CLITransport, CLISession
- `worker/transport/_sse.py` — SSETransport, SSESession

**Behavioral contracts:**

HTTPSession:
- `submit()` POSTs payload as JSON to endpoint.address + /task
- `receive()` yields ObservedEvent from response body (single result event)
- `cancel()` is idempotent no-op (HTTP is request-response)
- `close()` closes httpx client

CLISession:
- `submit()` writes JSON to subprocess stdin, closes stdin
- `receive()` yields ObservedEvent per stdout JSON line; result event terminates
- `cancel()` sends SIGTERM then SIGKILL after grace period
- `close()` waits for process exit, closes pipes

SSESession:
- `submit()` POSTs payload, opens SSE response stream
- `receive()` yields ObservedEvent per SSE `data:` line; result event terminates
- `cancel()` closes the HTTP connection
- `close()` closes httpx client

All adapters:
- Connect to an Endpoint (address + metadata)
- Inject gateway URL and token as payload fields
- Only result events and transport-level observations are yielded
- Progress/signal/artifact events travel via gateway

**Done when:**
- [ ] Each adapter has unit tests with a mock agent (subprocess/HTTP server)
- [ ] HTTPSession validated against R3 spike S3 scenario pattern
- [ ] CLISession validated against R3 spike S2 scenario pattern
- [ ] SSESession validated against existing AgentStream.sse_post behavior
- [ ] Error classification: connection refused -> TransportError, bad JSON -> ProtocolError, HTTP 400 -> AgentError
- [ ] mypy passes

---

### Phase 3: Execution backends

**What:** Implement backends conforming to ExecutionBackend protocol. Migrate existing
push providers.

**Files created:**
- `worker/execution/_subprocess.py` — SubprocessBackend (from R3 spike + local.py)
- `worker/execution/_docker.py` — DockerBackend (from R4 spike)

**Files migrated:**
- `worker/push_providers/cloudrun.py` -> `worker/execution/_cloudrun.py`
- `worker/push_providers/ecs.py` -> `worker/execution/_ecs.py`

CloudRun and ECS backends conform to ExecutionBackend protocol:
- `start()` does the existing fire-and-forget dispatch (RunTask / RunJob)
- Returns Endpoint with task ARN / execution ID as process_id
- `poll_status()` calls cloud API (GetExecution / DescribeTasks), returns JobStatus
- `stop()` / `kill()` — best-effort cancellation via cloud API

**Files removed:**
- `worker/_dispatch.py`
- `worker/push_providers/` (entire directory)

**Done when:**
- [ ] SubprocessBackend passes start/poll_status/stop/kill lifecycle test
- [ ] DockerBackend passes same lifecycle test (requires Docker daemon)
- [ ] CloudRunBackend.start() matches existing CloudRunDispatchBackend.submit() behavior
- [ ] CloudRunBackend.poll_status() returns correct JobStatus from GetExecution
- [ ] ECSBackend.start() matches existing ECSDispatchBackend.submit() behavior
- [ ] ECSBackend.poll_status() returns correct JobStatus from DescribeTasks
- [ ] All backends lazy-import their dependencies (docker, aioboto3, google-cloud-run)
- [ ] Old `DispatchBackend` protocol and `push_providers/` deleted
- [ ] mypy passes, no remaining imports of old paths

---

### Phase 4: Data plane gateway

**What:** Stateless HTTP service for agent-to-platform communication. Handles
artifacts, progress, and signals. JWT authentication. Runs embedded in worker
for dev, standalone for production.

**Files created:**
- `worker/gateway/__init__.py`
- `worker/gateway/_app.py` — Starlette app with embedded + standalone modes
- `worker/gateway/_auth.py` — JWT minting (for worker) and validation (for gateway)
- `worker/gateway/_routes.py` — artifact, progress, signal endpoints

**API endpoints:**

```
POST /artifacts/{task_id}       — write artifact (multipart upload)
GET  /artifacts/{task_id}/{key} — read artifact
POST /progress/{task_id}        — emit progress event
POST /signals/{task_id}         — emit signal
GET  /health                    — liveness check
```

**Authentication:**
- All mutating endpoints require `Authorization: Bearer {jwt}` header
- JWT contains: task_id, pool, scopes, exp
- Gateway validates token, checks task_id in URL matches token claim
- Dev mode: signing key is `monet-dev-key-not-for-production` constant

**Backend routing:**
- Gateway reads backend config (artifact store type/path, progress store DSN)
  from its own config or env vars
- Routes to configured backends via existing protocol abstractions
  (ArtifactWriter, ProgressWriter)

**Embedded mode:**
- Worker starts gateway on localhost:2027 during boot
- No auth required for localhost connections in dev mode
- Same Starlette app, just mounted in worker process

**Standalone mode:**
- `monet gateway serve` or Docker image
- Full JWT validation
- Connects to backend stores via config

**Done when:**
- [ ] Artifact write/read through gateway works end-to-end
- [ ] Progress emit through gateway works end-to-end
- [ ] Signal emit through gateway works end-to-end
- [ ] JWT validation rejects expired/invalid/wrong-task tokens
- [ ] Unknown task_id returns 404
- [ ] Concurrent tasks: no cross-talk
- [ ] Embedded mode starts with worker in `monet dev`
- [ ] Standalone mode starts via `monet gateway serve`
- [ ] Artifact content stays in configured store, pointer forwarded to CP
- [ ] mypy passes

---

### Phase 5: Workload execution

**What:** The composition layer. Two functions that sequence backend lifecycle +
transport session + lease renewal. Cloud-push polling loop.

**Files created:**
- `worker/workload/__init__.py`
- `worker/workload/_collect.py`
- `worker/workload/_managed.py`
- `worker/workload/_persistent.py`
- `worker/workload/_router.py`
- `worker/workload/_supervisor.py`

#### _collect.py — shared helpers

```python
async def _collect(session: Session) -> dict[str, Any]:
    """Wait for result event from transport session."""
    async for event in session.receive():
        if event.type == "result":
            return event.data
    raise ProtocolError("session ended without result event")


async def _run_with_lease(
    session: Session,
    queue: TaskQueue,
    task_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    """Collect result with concurrent lease renewal. Structured cancellation."""
    lease_task = asyncio.create_task(_renew_lease(queue, task_id))
    try:
        return await asyncio.wait_for(_collect(session), timeout=timeout_s)
    finally:
        lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)
```

#### _managed.py — per-task backend lifecycle

```python
async def execute_managed_workload(
    record: TaskRecord,
    agent: AgentEntryConfig,
    pool: PoolConfig,
    backend: ExecutionBackend,
    transport_factory: TransportAdapter,
    queue: TaskQueue,
    gateway_env: dict[str, str],
) -> AgentResult:
    endpoint = await backend.start(
        ContainerSpec(image=pool.image, entrypoint=agent.transport.cmd),
        {**gateway_env, **_task_env(record)},
    )
    try:
        await _wait_ready(backend, endpoint, pool.startup_timeout_s)
        session = await transport_factory.connect(endpoint)
        try:
            await session.submit(record.payload)
            result = await _run_with_lease(session, queue, record.task_id, pool.task_timeout_s)
            return _build_agent_result(result)
        except asyncio.TimeoutError:
            await session.cancel()
            raise TaskFailure("deadline exceeded")
        except AgentError as exc:
            raise TaskFailure(str(exc))
        finally:
            await session.close()
    finally:
        await backend.stop(endpoint, pool.graceful_shutdown_s)
```

#### _persistent.py — acquire from pool, release after

```python
async def execute_persistent_workload(
    record: TaskRecord,
    pool_name: str,
    router: TaskRouter,
    transport_factory: TransportAdapter,
    queue: TaskQueue,
) -> AgentResult:
    instance = await router.acquire_idle(pool_name)
    if instance is None:
        raise TaskFailure("pool is draining or all instances dead")
    try:
        session = await transport_factory.connect(instance.endpoint)
        try:
            await session.submit({"task_id": record.task_id, "payload": record.payload})
            result = await _run_with_lease(session, queue, record.task_id, router.task_timeout_s(pool_name))
            return _build_agent_result(result)
        except asyncio.TimeoutError:
            await session.cancel()
            raise TaskFailure("deadline exceeded")
        except AgentError as exc:
            raise TaskFailure(str(exc))
        finally:
            await session.close()
    finally:
        await router.release(pool_name, instance)
```

#### Cloud-push polling (new, replaces webhook flow)

```python
async def execute_cloud_push_workload(
    record: TaskRecord,
    pool: PoolConfig,
    backend: ExecutionBackend,
    queue: TaskQueue,
    gateway_url: str,
    token: str,
) -> AgentResult:
    """Dispatch to cloud, poll for completion, retrieve result from gateway."""
    gateway_env = {
        "MONET_GATEWAY_URL": gateway_url,
        "MONET_TOKEN": token,
    }
    endpoint = await backend.start(
        ContainerSpec(image=pool.image),
        {**gateway_env, **_task_env(record)},
    )
    lease_task = asyncio.create_task(_renew_lease(queue, record.task_id))
    try:
        while True:
            status = await backend.poll_status(endpoint)
            if status == JobStatus.SUCCEEDED:
                result = await _retrieve_result_from_gateway(gateway_url, record.task_id, token)
                return _build_agent_result(result)
            elif status == JobStatus.FAILED:
                raise TaskFailure("cloud job failed")
            await asyncio.sleep(pool.poll_interval_s)
    except asyncio.TimeoutError:
        await backend.kill(endpoint)
        raise TaskFailure("deadline exceeded")
    finally:
        lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)
```

#### _router.py — TaskRouter

Backend-agnostic idle/busy tracking. Used by all persistent workloads.

```python
class TaskRouter:
    async def acquire_idle(self, pool: str) -> ManagedInstance | None: ...
    async def release(self, pool: str, instance: ManagedInstance) -> None: ...
    def has_capacity(self, pool: str) -> bool: ...
    def task_timeout_s(self, pool: str) -> float: ...
```

#### _supervisor.py — ContainerSupervisor

Only used by subprocess and docker backends. K8s and cloud backends do NOT use this.

```python
class ContainerSupervisor:
    async def start_pool(self, pool: str, config: PoolConfig, backend: ExecutionBackend) -> list[ManagedInstance]: ...
    async def check_liveness(self, instance: ManagedInstance) -> bool: ...
    async def restart_instance(self, pool: str, instance: ManagedInstance) -> ManagedInstance: ...
    async def drain(self, pool: str) -> None: ...
    async def reconcile_orphans(self, pool: str, worker_id: str) -> int: ...
```

**Done when:**
- [ ] execute_managed_workload passes: start -> ready -> connect -> submit -> collect -> cleanup
- [ ] execute_managed_workload saga: failure at any step -> cleanup in reverse
- [ ] execute_managed_workload timeout: asyncio.wait_for -> cancel -> cleanup
- [ ] execute_persistent_workload: acquire -> submit -> collect -> release
- [ ] execute_cloud_push_workload: dispatch -> poll -> retrieve result from gateway -> complete
- [ ] execute_cloud_push_workload: cloud job failure -> queue.fail()
- [ ] TaskRouter: acquire blocks when no idle; release returns to idle
- [ ] ContainerSupervisor: warm pool startup, health loop, restart with backoff, circuit breaker, drain
- [ ] Docker supervisor delegates restart to Docker restart policy when available
- [ ] Orphan reconciliation kills containers from previous worker incarnation
- [ ] Structured cancellation: every create_task has cancel+await in finally

---

### Phase 6: Claim loop refactor

**What:** Replace the binary if/else (dispatch_backend vs in-process) with
pool-config-driven routing. Three paths: in-process, managed/persistent
(transport + backend), poll-and-collect (cloud push).

**File changed:** `worker/_loop.py`

**New routing logic:**

```python
async def _handle_task(record: TaskRecord, pools: dict[str, PoolConfig]) -> None:
    pool = pools.get(record.pool, pools.get("local"))
    agent = agent_registry.get(record.agent_id)

    match pool.backend:
        case "in_process":
            await _execute_in_process(record)

        case "cloudrun" | "ecs":
            gateway_url = pool.gateway or default_gateway_url
            token = mint_task_token(record, pool)
            result = await execute_cloud_push_workload(
                record, pool, _resolve_backend(pool), queue, gateway_url, token,
            )
            await queue.complete(record.task_id, result)

        case "subprocess" | "docker" | "kubernetes":
            transport = _resolve_transport(agent)
            backend = _resolve_backend(pool)
            gateway_env = _build_gateway_env(record, pool)
            if pool.workload == "persistent":
                result = await execute_persistent_workload(record, pool.name, router, transport, queue)
            else:
                result = await execute_managed_workload(record, agent, pool, backend, transport, queue, gateway_env)
            await queue.complete(record.task_id, result)
```

**Back-pressure:** Before claiming, check `router.has_capacity(pool)` for each
persistent pool. If all saturated, back off 0.3s.

**Multi-pool support:** Worker claims from multiple pools. `run_worker()` accepts
`pools: list[str]` instead of single `pool: str`.

**Worker startup:**
1. Load pool configs and gateway config from monet.toml
2. Start gateway (embedded mode) on configured port
3. If tunnel configured, start cloudflared sidecar
4. For each docker/subprocess persistent pool: reconcile orphans, start warm pool
5. Enter claim loop

**Graceful shutdown:**
1. Stop claiming (exit while loop)
2. Drain all persistent pools
3. Wait for in-flight cloud-push poll loops
4. Wait for in-flight task handles
5. Stop gateway
6. Exit

**Done when:**
- [ ] Three execution paths work: in-process, managed/persistent, cloud-push poll
- [ ] Same worker serves multiple pool types simultaneously
- [ ] Gateway starts embedded with worker
- [ ] Cloud-push tasks get MONET_GATEWAY_URL and MONET_TOKEN injected
- [ ] Back-pressure prevents over-claiming for persistent pools
- [ ] Graceful shutdown: zero orphaned containers, all in-flight results forwarded
- [ ] Old `dispatch_backend` parameter removed from run_worker()
- [ ] Old `push_providers/` imports removed
- [ ] E2E test: in-process @agent + subprocess CLI agent + docker HTTP agent in same worker

---

### Phase 7: Agent loader migration + cleanup

**What:** Rewire agent_loader to use transport adapters. Remove AgentStream.

**Files changed:**
- `core/agent_loader.py` — `_make_handler()` and `_build_stream()` rewritten

**New handler generation:**

```python
def _make_handler(transport: AgentTransportConfig, agent_id: str, command: str) -> Any:
    async def handler(task: str, context: list[dict[str, Any]] | None = None) -> str | None:
        payload = {"task": task, "context": context or [], "command": command, "agent_id": agent_id}
        adapter = _resolve_adapter(transport)
        endpoint = Endpoint(address=transport.url or "", process_id="direct", backend_type="none")
        session = await adapter.connect(endpoint)
        try:
            await session.submit(payload)
            async for event in session.receive():
                if event.type == "result":
                    output = event.data.get("output")
                    return output if isinstance(output, str) else None
        finally:
            await session.close()
        return None
    return handler
```

Note: in the full worker execution path, the handler is NOT called directly.
The worker's workload execution function manages the transport. The handler above
is the fallback for direct invocation outside a worker context.

**Files removed:**
- `streams.py` — entire file
- `handlers.py` — entire file

**Public API change:**
- `from monet import AgentStream` — removed. Breaking change.
- `from monet import webhook_handler, log_handler` — removed.

**Done when:**
- [ ] agent_loader registers agents that work through transport adapters
- [ ] `[[agent.on]]` in agents.toml rejected with clear deprecation message
- [ ] `streams.py` deleted
- [ ] `handlers.py` deleted
- [ ] `__init__.py` no longer exports AgentStream, webhook_handler, log_handler
- [ ] All tests pass without AgentStream imports
- [ ] mypy passes

---

## Open design questions (resolve during implementation)

1. **ContainerSpec shape:** What fields? image, entrypoint/cmd, env, resource limits,
   labels? Keep minimal — only what backends need to start a process/container.

2. **How does the worker know agent transport type at claim time?** Option (b):
   worker loads agents.toml at startup and keeps a local agent config lookup.

3. **Subprocess + HTTP transport:** SubprocessBackend assigns a port via
   MONET_AGENT_PORT env var (R3 spike pattern). Agent binds it, readiness
   check confirms.

4. **K8s backend implementation details:** Deferred. Separate spike when there's
   a K8s user.

5. **OTel from cloud-push agents:** Should gateway proxy OTel spans, or should
   agents export OTLP directly to a configured collector? Direct OTLP export is
   standard practice. Likely: OTel is direct-to-collector, gateway handles
   artifacts + progress + signals only. Collector endpoint passed as
   `MONET_OTEL_ENDPOINT` env var alongside gateway URL.

6. **Cloud-push result retrieval:** Agent writes result to gateway as artifact
   with well-known key (e.g., `_result`). Worker retrieves from gateway after
   poll_status returns SUCCEEDED. Need to define this contract.

7. **Tunnel lifecycle in compose:** When `gateway.tunnel = "cloudflare"`, the
   compose stack needs a cloudflared sidecar. How is the tunnel URL discovered
   and injected into pool config? Likely: cloudflared logs the URL, a startup
   script parses it and sets an env var.

---

## Phase ordering and dependencies

```
Phase 0 (config)
    |
Phase 1 (protocols) ──────────────────────────┐
    |                                          |
Phase 2 (transports)     Phase 3 (backends)    Phase 4 (gateway)
    |                        |                 |
    +────────────────────────+─────────────────+
                             |
                       Phase 5 (workload execution)
                             |
                       Phase 6 (claim loop refactor)
                             |
                       Phase 7 (agent loader + cleanup)
```

Phases 2, 3, 4 can run in parallel after Phase 1.
Phase 5 depends on 2 + 3 + 4.
Phases 6-7 are sequential.

Original Phase 8 (server webhooks) removed entirely per ADR-007.

---

## Testing strategy

- Phases 1-4: unit tests per module. Mock agents (HTTP server, subprocess) for
  transport tests. Docker tests require Docker daemon (mark with pytest marker).
  Gateway tests use httpx test client.
- Phase 5: integration tests composing real transport + real backend + workload
  execution. Cloud-push polling tested with mock cloud API responses.
- Phase 6: E2E test with multiple pool types in one worker.
- Phase 7: verify agent_loader migration doesn't break existing @agent tests.

All tests follow existing convention: `tests/test_*.py`, async mode auto,
`-q 2>&1 | tail -60`, `--ignore=tests/e2e --ignore=tests/compat --ignore=tests/chat`.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Docker tests flaky in CI | Medium | Medium | Docker tests behind marker, run in separate CI job |
| AgentStream removal breaks external users | Low | High | Check if any example or doc references AgentStream directly |
| Claim loop refactor introduces regression in existing in-process path | Medium | High | Phase 6 tests must prove zero regression for in-process @agent |
| Windows: asyncio subprocess behavior differs | Medium | Medium | CLITransport tests run on Windows CI |
| Cloud API rate limits during polling | Low | Medium | Configurable poll_interval_s, exponential backoff ceiling |
| Cloudflare Tunnel reliability for local+cloud-push | Low | Low | Tunnel is optional convenience; user can deploy gateway with own ingress |
| JWT signing key management in production | Low | Medium | Standard secret management; documented in deployment guide |
