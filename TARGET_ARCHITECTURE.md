# Monet Platform — Target Architecture Brief

## Overview

Monet is a multi-agent orchestration platform. Developers bring agents in any
technology — Python functions, CLIs, HTTP services, SSE streams — and get
orchestration, coordination, and observability without building it themselves.

The platform separates three concerns that must never mix:

- **Orchestration** — graph topology, task routing, state tracking
- **Execution** — agent invocation, output serialisation
- **Infrastructure** — URLs, credentials, cloud-specific config

---

## Mental Model

The compiled LangGraph StateGraphs are the **factory floor** — fixed
infrastructure that does not change at runtime. Agents are **machines installed
at stations** on that floor. Machines can come and go without rebuilding the
factory. The orchestration server is the **floor supervisor** — it knows which
stations exist and routes work to them. Workers and cloud services are the
**machine operators** — they execute work and report back.

---

## Core Separation: Orchestration vs Execution

**Orchestration server** owns:
- Graph topology and routing logic
- Capability manifest — which agents are available and where
- Task queue — work items and their lifecycle
- State tracking — LangGraph checkpointer, wave results as pointers
- Infrastructure config — pool URLs, auth (orchestrator only, never workers)

**Workers and cloud services** own:
- Agent handler registries — local `@agent` decorator registrations
- Agent invocation — Python, CLI, HTTP, SSE
- Output serialisation — writes to catalogue, returns pointer
- Nothing else — no routing, no graph knowledge, no infra config

**Content never enters the orchestration plane.** Agent outputs are serialised
to the catalogue on the execution side. The orchestration server holds only
pointers, signals, and metadata.

---

## Agent Definition

Developers declare agents with a decorator. Pool assignment is the only
orchestration concern in agent code. Everything else is execution detail.

The decorator wraps any return value — `str`, `dict`, or `None` — into
`AgentResult` via `_wrap_result`. Functions declare only the
`AgentRunContext` fields they need as parameters. No return type annotation
is required or expected.

```python
# Python native
@agent("writer", command="draft", pool="default")
async def write_draft(task, context):
    ...  # return str, dict, or None — decorator handles the envelope

# CLI — wrapped with AgentStream, identical interface to the worker
@agent("transcriber", command="run", pool="default")
async def transcribe(task):
    return await AgentStream.cli(["whisper-cli", "--task", task]).run()

# SSE — wrapped with AgentStream
@agent("researcher", command="deep", pool="cloud")
async def research_deep(task, context):
    return await AgentStream.sse(
        "http://research-service:9000/stream",
        json={"task": task, "context": context},
    ).run()

# HTTP polling — wrapped with AgentStream
@agent("pipeline", command="run", pool="default")
async def run_pipeline(task):
    return await AgentStream.http(
        "http://pipeline-service:8080/status",
        interval=2.0,
    ).run()
```

`AgentStream` is the universal adapter for external agents. CLI, SSE, and
HTTP transports all produce the same typed event stream — progress, signal,
artifact, result — consumed by the decorator's standard envelope. No
separate config file is needed for non-Python agents. Every agent, regardless
of transport, is a decorated Python function from the worker's perspective.

---

## Pool Types

Three pool types. The orchestrator is the only party that knows which type
a pool is and what infrastructure backs it.

| Type | Description | Worker needed |
|---|---|---|
| `local` | In-process on orchestration server. Platform-internal agents only (planner, triage). | No |
| `pull` | Workers poll the task queue. Long-lived processes in any environment. | Yes |
| `push` | Orchestrator pushes tasks to a cloud endpoint (e.g. Cloud Run). Stateless, scales to zero. | No |

Pool topology is declared in `monet.toml` on the orchestration server.
Infrastructure details (URLs, auth) are injected via environment variables —
never committed to source control.

```toml
# monet.toml — committed, no sensitive values
[pools.local]
type = "local"

[pools.default]
type = "pull"
lease_ttl = 300

[pools.cloud]
type = "push"
```

```bash
# Deployment environment — injected, never committed
MONET_POOL_CLOUD_URL=https://agents-xyz.run.app/invoke
MONET_POOL_CLOUD_AUTH=gcp-oidc
```

---

## Capability Manifest

Server-side live map of `(agent_id, command) → dispatch config`.
Populated from deployment records created by `monet register`.

| Pool type | Available when |
|---|---|
| local | Server process running + module imported |
| pull | Deployment active + ≥1 worker heartbeating in pool |
| push | Deployment active (infrastructure availability is the cloud platform's concern) |

`invoke_agent` checks the manifest before every dispatch. Missing or unavailable
capability returns a `CapabilityUnavailable` signal. The existing wave reflection
and HITL machinery handles it — the line stops cleanly at the right station.

---

## Task Queue

Lightweight persistent queue. SQLite-backed initially, swappable.
The only party that writes to it is the orchestrator. The only parties
that read from it are pull workers.

**Task lifecycle:**
```
SCHEDULED → PENDING → RUNNING → COMPLETED
                              → FAILED
                              → CRASHED  (lease expired, no heartbeat)
```

Lease-based claiming — workers claim tasks with a TTL. A background sweeper
requeues tasks whose leases expire without a result. Push tasks are logged in
the queue for observability but not claimed by workers — the orchestrator
dispatches them directly.

---

## `invoke_agent` — The Single Dispatch Seam

Graphs, node wrappers, and state schemas never change. `invoke_agent` is the
only function that knows about transport.

```
invoke_agent(agent_id, command, ctx)
  → check capability manifest
  → if unavailable: return AgentResult with CapabilityUnavailable signal
  → match dispatch type:
      local → call handler directly, no queue
      pull  → enqueue task, await result event
      push  → enqueue task (for observability), HTTP POST to pool URL, await result event
  → return AgentResult { success, pointer, signals }
```

Both pull and push paths converge on the same result event mechanism.
The node wrapper above `invoke_agent` is unaware of which path was taken.

---

## Worker

A thin process developers run in their execution environment. No knowledge
of graphs, routing, or infrastructure.

**What it knows:** server URL, API key. Nothing else.

**Startup sequence:**
1. Scan working directory for `@agent` decorated functions (AST scan then import)
2. Decorator side effects populate local handler registry
3. Derive capability list from registry
4. Register capabilities with orchestration server
5. Start heartbeat loop (30s interval)
6. Start poll loop (2s interval)

Every agent — regardless of whether it calls an LLM, spawns a subprocess,
hits an SSE stream, or polls HTTP — is a decorated Python function. The
worker only ever calls decorated callables. Transport complexity lives inside
the function body via `AgentStream`, not in the worker itself.

```bash
# Run from directory containing agent modules, or with installed package
cd /my-project
monet worker

# Or point at a specific path
monet worker --path ./agents
```

**Worker environment — minimal:**
```bash
MONET_SERVER_URL=https://orchestration.example.com
MONET_API_KEY=xxx
# Plus whatever the agents themselves need: LLM keys, CLI tools on PATH, etc.
```

No pool URLs. No cloud credentials. No routing config.
The worker executes what arrives in its queue and reports back.

---

## Registration — `monet register`

Deployment-time CLI command. Runs in CI/CD. Creates deployment records
on the orchestration server so the capability manifest is populated before
any worker starts.

```bash
# Scans cwd for @agent decorators
# Groups capabilities by pool declared in each decorator
# Creates one deployment record per pool
monet register
```

Discovery is two-phase: AST scan to find files containing `@agent` decorators
(safe, no code execution), then import only those files to extract registration
metadata. Non-Python agents wrapped with `AgentStream` are discovered the same
way — they are decorated Python functions like any other.

Pull pools: `monet register` followed by starting the worker process.
Push pools: `monet register` is the only step — no worker needed.

**CI/CD integration:**
```yaml
- run: pip install monet .
- run: monet register
  env:
    MONET_SERVER_URL: ${{ secrets.MONET_SERVER_URL }}
    MONET_API_KEY: ${{ secrets.MONET_API_KEY }}
```

---

## Cloud Run (Push Pool)

A standard HTTP service. No worker process, no polling. Receives tasks
from the orchestrator, executes agents, posts result pointers back.

- Imports agent modules — `@agent` decorators populate local registry
- Single `/invoke` endpoint routes by `(agent_id, command)` in task payload
- Writes output to catalogue, POSTs pointer to orchestration server
- Auth between orchestrator and Cloud Run handled at platform level (GCP OIDC)
  — no credentials in application code
- Scales to zero when idle, scales out automatically under wave fan-out load

**Cloud Run environment:**
```bash
MONET_SERVER_URL=https://orchestration.example.com
MONET_API_KEY=xxx          # scoped result-posting key only
MONET_CATALOGUE_BUCKET=... # or filesystem path
```

---

## Three-Layer Separation

```
Layer           Owner           Contains
──────────────────────────────────────────────────────────────────
Agent code      Developer       @agent decorator, pool name, handler logic
Pool config     Server toml     Pool names, types, lease TTLs
Infrastructure  Environment     URLs, auth tokens, cloud config
```

No layer contains details from another. Agent code has no URLs. Pool config
has no credentials. Infrastructure config has no business logic. A developer
can write, test, and run agents locally with no cloud credentials anywhere
in their environment.

---

## What Does Not Change

The following are unaffected by the task queue transition:

- LangGraph StateGraph topology and compilation
- State schemas (`EntryState`, `PlanningState`, `ExecutionState`, `WaveItem`, `WaveResult`)
- Node wrapper signal handling and HITL interrupt logic
- Wave fan-out via LangGraph `Send`
- Checkpointer and thread management
- Client SDK streaming and HITL resume patterns

The only change visible above `invoke_agent` is that `AgentResult.output`
is always a catalogue pointer. Raw content never appears in state.

---

## New Modules Required

| Module | Responsibility |
|---|---|
| `monet/server/bootstrap.py` | Startup sequence: tracing → catalogue → manifest → task queue → worker listener |
| `monet/server/manifest.py` | `CapabilityManifest` — live capability map derived from deployment records |
| `monet/server/queue.py` | `TaskQueue` — enqueue, claim, complete, fail, requeue expired |
| `monet/server/dispatcher.py` | Pull vs push dispatch; only party with infrastructure knowledge |
| `monet/server/routes.py` | Worker registration, heartbeat, result callback endpoints |
| `monet/worker/process.py` | `WorkerProcess` — heartbeat, poll, execute, report |
| `monet/worker/client.py` | `WorkerClient` — register, heartbeat, claim, complete, fail |
| `monet/cli/register.py` | `monet register` — AST discovery, pool grouping, deployment record creation |
| `monet/cli/worker.py` | `monet worker` — CLI entry point for worker process |
| `monet/client/` | SDK client utilities: `drain_stream`, `get_state_values`, state initialisers, node name constants |

`monet/streams.py` (`AgentStream`) already exists and requires no changes.
It is the transport adapter for all non-Python agents. The worker registry
holds only decorated callables — transport is encapsulated inside function
bodies, not in the worker infrastructure.

## Modules Removed

| Module | Reason |
|---|---|
| `monet/orchestration/_validate.py` | Build-time registry checks replaced by dispatch-time manifest checks |
| `monet/orchestration/_content_limit.py` | Replaced by execution-side serialisation — outputs are always pointers |
| `monet/orchestration/_run.py` | In-process execution not a supported production path |

---

## Build Order

1. `MonetConfig` and `monet.toml` parser
2. `CapabilityManifest` — initialised from deployment records, three entry types
3. `TaskQueue` — enqueue, claim, lease sweeper
4. `invoke_agent` — manifest check, dispatch branch, `_await_result`
5. Server endpoints — registration, heartbeat, result callback
6. `WorkerProcess` and `InvokerRegistry` — pull worker with all invoker types
7. `WorkerClient` — shared result-posting library
8. `monet register` CLI — AST discovery, pool grouping, deployment record creation
9. Cloud Run service scaffold — generic `/invoke` endpoint
10. `monet.client` SDK utilities — extract from example `workflow.py`
11. Pointer-only state — update `AgentStateEntry`, remove `_content_limit.py`
