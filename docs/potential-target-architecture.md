# monet — target architecture for multi-agent orchestration

This document summarizes the architecture and design patterns for extending monet to support heterogeneous agent runtimes, including always-on agents like OpenClaw, ephemeral cloud-dispatched agents, and everything in between.


## Core principle

Safety is topological, not behavioral. Agents are untrusted black boxes. The orchestrator is deliberately dumb — it consumes signals and makes routing decisions. It never inspects agent internals, never enforces agent behavior, and never assumes agent cooperation.


## The two-axis composition model

Transport and execution backend are orthogonal concerns. They compose independently via pool configuration. You build N + M + K small components, not N x M x K integration packages.

**Transport adapters** answer: given a running agent, how do I exchange messages with it?

- **Direct** — in-process function call via `@agent` decorator. No wire protocol.
- **HTTP** — POST request, response body contains result. Long polling is an implementation detail for slow agents.
- **SSE** — server-sent event stream. Agent streams progress events, final event contains result.
- **MCP** — bidirectional session. Worker holds MCP client to agent's MCP server. Agent connects back to worker's MCP tool bridge. Two channels, two chokepoints.
- **CLI** — stdin/stdout pipes to a subprocess. JSON in, JSON out.
- **Callback** — REMOVED (see ADR-007). Previously: agent POSTs result back to server. Replaced by worker-managed poll-and-collect. Worker dispatches to cloud backend, polls cloud API for completion, retrieves result from data plane gateway.

Each connected adapter implements a uniform protocol surface:

```
connect(endpoint) -> Session
submit(session, task) -> None
receive(session) -> AsyncIterator[Event]
cancel(session) -> None
close(session) -> None
```

**Execution backends** answer: how do I make the agent reachable and manage its lifecycle?

- **In-process** — registry lookup, direct function call. No lifecycle management.
- **Subprocess** — `os.spawn`, signal-based lifecycle.
- **Docker** — container API. Start, stop, kill, health check.
- **Kubernetes** — pod API via K8s client. Same lifecycle semantics as Docker but managed by K8s controllers.
- **Cloud Run** — GCP Job API. Fire-and-forget, agent calls back.
- **ECS / Fargate** — AWS RunTask API. Fire-and-forget, agent calls back.

Each backend implements:

```
start(image, config) -> Endpoint
poll_status(endpoint) -> JobStatus       # running | succeeded | failed | unknown
stop(endpoint, grace_period) -> None
kill(endpoint) -> None
```

Cloud backends (CloudRun, ECS) implement `poll_status` via cloud API
(`GetExecution`, `DescribeTasks`). Local backends (subprocess, Docker) check
process/container status directly. See ADR-007.


## Workload types

Workload type is a deployment decision, not an agent property. The same agent can run as any workload type depending on pool assignment. Workload type determines the lifecycle policy the worker applies.

**Task** — stateless, run-to-completion. Worker starts the agent (or calls it in-process), submits one task, collects the result, tears down. This is what monet has today.

**Session** — heavier startup, still scoped to a single task. Container starts, transport connects (e.g. MCP session negotiation), task executes, session tears down, container stops. Appropriate for agents with nontrivial initialization but no cross-task state.

**Persistent** — always-on. Container runs independently of any single task. Worker maintains a persistent transport connection and submits work into the running agent. Lifecycle includes restart policy, heartbeat monitoring, and optional warm pool. Container persists across tasks and accumulates internal state.

The workload manager is the composer — the only piece that touches both the transport adapter and the execution backend. For task workloads, it sequences: `backend.start()` then `transport.connect()` then `transport.submit()` then wait then `transport.close()` then `backend.stop()`. For persistent workloads, it calls `backend.start()` once, holds the transport connection open, monitors health, and applies restart policy.

Design pattern reference: Erlang/OTP supervision trees (permanent / transient / temporary child processes), Kubernetes controllers (Deployment vs Job vs CronJob), Temporal activities vs workflows.


## Pool configuration

The pool is the binding point where transport, backend, and workload type compose. Agent developers never touch pool config. Platform team owns it. Promoting an agent from dev to prod is a pool reassignment, not a code change.

```toml
# pools.toml — owned by platform team

[pool.local]
workload = "task"
transport = "direct"
backend = "in-process"

[pool.docker-mcp]
workload = "session"
transport = "mcp"
backend = "docker"
image = "openclaw:latest"

[pool.docker-mcp.lifecycle]
startup_timeout_s = 30
graceful_shutdown_s = 15

[pool.docker-mcp.mcp]
server_endpoint = "stdio"

[pool.docker-mcp.tool_bridge]
expose = ["filesystem", "web_search"]
deny = ["shell", "email_send"]

[pool.persistent-mcp]
workload = "persistent"
transport = "mcp"
backend = "docker"
image = "openclaw:latest"

[pool.persistent-mcp.lifecycle]
restart_policy = "on_failure"
heartbeat_interval_s = 30
graceful_shutdown_s = 30
warm_pool_size = 2

[pool.cloud-burst]
workload = "task"
backend = "cloudrun"
project = "my-project"
region = "us-central1"
job = "monet-worker"
poll_interval_s = 5
gateway = "https://dp.example.com"         # agents POST artifacts/progress here
```


## Data plane gateway

All agents communicate with monet's shared services (artifact store, progress
store, signals) through a data plane gateway. No direct backend access from
agents, no worker localhost shortcut. See ADR-008 for full rationale.

The gateway is a stateless HTTP service:

```
POST /artifacts/{task_id}       write artifact
GET  /artifacts/{task_id}/{key} read artifact
POST /progress/{task_id}        emit progress event
POST /signals/{task_id}         emit signal
```

Agents authenticate with a task-scoped JWT (`MONET_GATEWAY_URL` + `MONET_TOKEN`
env vars). Gateway validates and routes to configured backend stores. Backend
credentials live only in the gateway — agents never see them.

**Deployment modes:**
- `monet dev`: embedded in worker process on localhost (automatic, invisible)
- Local + cloud push: container in Docker Desktop + Cloudflare Tunnel for
  public URL (zero-account quick tunnels via trycloudflare.com)
- Production: standalone container behind load balancer
- Managed (future): monet-hosted service

**Agent access:** Any runtime that can make HTTP POST with bearer token works.
MCP sidecar tools are thin HTTP clients. monet CLI (`monet artifact write`)
reads the same env vars. No SDK dependency required for agents to participate.

**Enterprise topology:** Single ingress point. Gateway holds all backend
credentials. Workers and agents are outbound-only. See ADR-008.


## Agent configuration

Agent declarations are purely about capability, signal mapping, and pool assignment. No infrastructure details.

```toml
# agents.toml — owned by agent developers

[[agent]]
id = "openclaw-researcher"
pool = "docker-mcp"
command = "research"
description = "Deep research via OpenClaw"

[agent.signals]
tool_unavailable = "mcp_error.code == -32601"
semantic_error = "mcp_error.code == -32603"
low_confidence = "output.metadata.confidence < 0.7"
content_offloaded = "output.size_bytes > 50000"
```


## Signal system

Signals are the only interface between agent execution and orchestration routing. The orchestrator routes on signals without knowing which workload type, transport, or backend produced them.

Three independent sources generate signals — only one requires agent cooperation:

1. **Structural** — DAG topology creates gates. A HITL node fires because the graph edge says it fires, regardless of what the agent wants. The agent can't skip the gate because it's in a different process.

2. **Adapter-derived** — the transport adapter watches protocol-level events. Tool call denied by policy? `TOOL_UNAVAILABLE`. MCP error code? `SEMANTIC_ERROR`. Heartbeat missed? `AGENT_UNRESPONSIVE`. These require zero agent cooperation.

3. **Convention-based** — if the agent returns structured metadata (confidence scores, flags), the adapter maps them to signals via the config expressions. This is optional and best-effort.

Signal types partition into three routing groups:

- **Control flow** (BLOCKING group) — orchestrator routes directly: `NEEDS_HUMAN_REVIEW`, `ESCALATION_REQUIRED`, `APPROVAL_REQUIRED`, `TOOL_UNAVAILABLE`, `DEPENDENCY_FAILED`, etc.
- **Informational** (RECOVERABLE group) — feeds QA reflection: `LOW_CONFIDENCE`, `PARTIAL_RESULT`, `CONFLICTING_SOURCES`, etc.
- **Audit** — recorded in state, no routing consequence: `EXTERNAL_ACTION_TAKEN`, `CONTENT_OFFLOADED`, `SENSITIVE_CONTENT`.


## MCP bidirectional topology

For MCP-capable agents, the worker maintains two independent MCP channels:

**Channel 1: task submission.** The worker holds an MCP client connected to the agent's MCP server. The worker submits tasks, receives results, and extracts signals. This is the "submit work" path.

**Channel 2: tool bridge.** The agent holds an MCP client connected to the worker's MCP tool bridge server. The agent calls tools (filesystem, web search, etc.) through this bridge. The bridge enforces the allow/deny policy from pool config and executes approved calls with scoped credentials the agent never sees.

These two channels form two chokepoints the agent cannot bypass. The agent can't access tools except through the bridge. The agent can't talk to the orchestration plane except through the worker. Credential isolation is enforced by construction.


## Cancel / abort lifecycle

Three-tier shutdown for MCP agents, sequenced by the workload manager:

1. **MCP cancel request** — graceful. Ask the agent to stop via MCP. Agent can checkpoint, flush state, release resources. Timeout configurable via `graceful_shutdown_s`.

2. **Close MCP sessions** — both directions. Cuts the agent off from tools and from receiving new work. The agent is now isolated even if still running.

3. **Container kill** — `docker stop` then `docker kill`. Hard abort. The execution backend handles this.

For non-MCP agents, the lifecycle reduces to: transport cancel (HTTP abort, SSE disconnect, SIGTERM for subprocess) then backend kill.


## Heartbeat and health monitoring

For persistent workloads, the workload manager periodically pings the agent through the transport adapter. If the agent stops responding within the configured timeout, the worker emits an `AGENT_UNRESPONSIVE` signal. The orchestrator decides what to do: wait, restart, escalate, or kill.

The existing `WorkerClient.heartbeat_with_tracking()` pattern (escalating log levels on consecutive failures, auto-recovery on success) is the right model for transport-level health monitoring.


## Worker claim loop changes

The current claim loop has two paths: in-process execution and dispatch backend fire-and-forget. The target architecture adds a third path driven by pool config:

```python
match pool_config.strategy:
    case "in-process":
        # Existing path: registry lookup, execute_task()
        task = asyncio.create_task(_execute(record))

    case "docker" | "subprocess" | "kubernetes":
        # New path: workload manager composes backend + transport
        handle = await workload_manager.ensure_running(agent_id, pool_config)
        await workload_manager.submit(handle, record)
        # Result comes back via transport adapter -> queue.complete()

    case "cloudrun" | "ecs":
        # Poll-and-collect: dispatch, poll cloud API, retrieve result from gateway
        gateway_url = pool_config.gateway or default_gateway_url
        token = mint_task_token(record, pool_config)
        endpoint = await execution_backend.start(spec, {"MONET_GATEWAY_URL": gateway_url, "MONET_TOKEN": token})
        while (status := await execution_backend.poll_status(endpoint)) == JobStatus.RUNNING:
            await asyncio.sleep(pool_config.poll_interval_s)
        result = await retrieve_result_from_gateway(gateway_url, record.task_id, token)
```

The `DispatchBackend` protocol is replaced by `ExecutionBackend`. Cloud-push backends gain `poll_status()` for worker-managed result collection (ADR-007). A `WorkloadManager` composes an `ExecutionBackend` with a `TransportAdapter` based on pool config.


## How invoke_agent stays unchanged

`invoke_agent()` enqueues a task, waits for completion via `await_completion()`, and returns an `AgentResult` with signals. It doesn't know or care what the worker does. Whether the worker calls an `@agent` function, talks MCP to a Docker container, or fires a Cloud Run job — `invoke_agent()` sees the same thing: task in, result out, signals routed.

The one change needed: `task_timeout` should come from pool config rather than a global `OrchestrationConfig` value. A classifier needs 30 seconds. A deep research agent needs 6 hours. The timeout is a pool property.


## How LangGraph orchestration stays unchanged

The planning graph creates `RoutingSkeleton` with `RoutingNode` entries and `depends_on` edges. The execution graph dispatches waves based on `ready_nodes()`. The `SignalRouter` maps signal groups to routing actions (BLOCKING -> interrupt, RECOVERABLE -> retry). HITL gates are `interrupt()` calls triggered by signals.

None of this changes. The planner places quality gates based on task semantics. The execution graph routes on signals. Transport adapters, execution backends, and workload managers are all below the queue boundary — invisible to the orchestration layer.


## Progressive trust model

Trust expands via pool promotion, not code changes. Each step is pulled by demonstrated track record (Toyota P3).

**Step 1 — Personal worker.** `monet dev` on laptop. Pool config: `workload=task, backend=docker, transport=mcp`. Container on same machine. Blast radius: your machine. OTel traces build track record.

**Step 2 — Team workers.** Multiple users, each running `monet worker`. Pool config unchanged. Shared server, per-user pools. Track record visible to team via Langfuse dashboard.

**Step 3 — Orchestration SaaS.** Control plane hosted. Data plane on customer machines. Pool promotion: `workload=persistent` for agents with proven track record. Container lifecycle managed by warm pool.

**Step 4 — Centralized fleet.** Workers move to VPS or cloud. Pool promotion: `backend=ecs` or `backend=kubernetes`. Security team manages fleet. Full automation with risk-scored auto-approval replacing manual HITL.

Each transition is a pool config change. Same agents, same pipelines, same signal mappings.


## What to build, in order

1. **Pool config parser** — read `pools.toml`, produce typed `PoolConfig` objects. Small, testable, no runtime dependencies.

2. **Transport adapter protocol** — define the 5-method interface. Implement HTTP and SSE adapters first (you already have `AgentStream` patterns to extract from). MCP adapter comes when OpenClaw integration starts.

3. **Execution backend protocol** — rename `DispatchBackend` to `ExecutionBackend`. Add `start/health_check/stop/kill` methods. Your existing ECS and CloudRun backends need thin wrappers to conform.

4. **Workload manager** — the composer that binds transport + backend + lifecycle policy. Start with task workload (trivial — it's what you have). Add session workload (start, connect, submit, teardown). Persistent comes last (restart policy, heartbeat, warm pool).

5. **Worker claim loop refactor** — replace the binary `if dispatch_backend` branch with pool-config-driven routing. Three paths: in-process, managed (workload manager), and fire-and-forget (existing dispatch).

6. **MCP transport adapter + tool bridge** — the OpenClaw-specific work. Bidirectional MCP, capability negotiation, tool interception, credential scoping. This is the largest single piece but builds on the generic transport adapter protocol.

7. **Signal extraction layer** — generalize signal mapping from agent config. MCP error codes, HTTP status codes, output metadata expressions. Uniform extraction regardless of transport.


## Design patterns referenced

- **Erlang/OTP supervision trees** — permanent, transient, temporary child process types with restart policies. Maps to persistent, session, task workload types.
- **Kubernetes controllers** — Deployment (always-on), Job (run-to-completion), CronJob (scheduled). Same container runtime, different lifecycle semantics. Maps to pool config selecting workload type independently of backend.
- **Temporal durable execution** — workflows that sleep, wake on signals, checkpoint state, survive infrastructure failures. Available as an opt-in for high-stakes pipelines, not baked into the worker.
- **Toyota Production System** — P3 (pull systems: trust expanded by track record, not pushed by assumption), P5 (jidoka: build quality in structurally, don't inspect it in behaviorally), P8 (proven technology only in the critical path).
- **A2A / pi-agent** — agents as opaque capability units. The orchestrator preserves opacity. Agents collaborate without exposing internals.


## Related ADRs

- **ADR-007** — Cloud-push result delivery via polling, not webhooks. Callback transport removed.
- **ADR-008** — Data plane gateway for cross-network service access. Always-gateway architecture, credential isolation, pool-scoped service config, Cloudflare Tunnel pattern.