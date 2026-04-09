# Orchestration

The orchestration layer integrates monet agents with LangGraph. It provides a three-graph supervisor topology, a task queue for decoupled dispatch, and pointer-only state management.

## Three-graph topology

Every user message flows through three graphs with clean handoffs:

1. **Entry graph** — triage via planner/fast. Classifies as simple or complex.
2. **Planning graph** — planner/plan generates a work brief. Human approval gate with bounded revision count.
3. **Execution graph** — wave-based parallel execution via LangGraph `Send`. QA reflection gates. Retry budget. Signal routing.

See [Graph Topology](../architecture/graph-topology.md) for the full topology diagrams.

## State schemas

Each graph has its own TypedDict state:

- `EntryState` — task, triage result, trace/run IDs
- `PlanningState` — task, work brief, planning context, human feedback, revision count
- `ExecutionState` — work brief, phase/wave indices, wave results (append-only), wave reflections, signals, abort reason, pending context

State is pointer-only: `pending_context` entries contain summaries and catalogue artifact pointers, never full content. Agents that need upstream content call `resolve_context()`.

## Agent invocation

`invoke_agent()` dispatches via the configured task queue:

```python
from monet.orchestration import invoke_agent

result = await invoke_agent("researcher", command="deep", task="quantum computing")
```

The dispatch flow:

1. Check capability manifest — if agent not declared, return `CAPABILITY_UNAVAILABLE` signal instantly
2. Look up pool from manifest
3. Enqueue task to the pool's queue
4. Poll for result (with configurable timeout via `MONET_AGENT_TIMEOUT`)
5. On timeout, cancel the task to prevent wasted execution

Transport (local call, HTTP, cloud forwarding) is the worker's concern, not the orchestrator's. The queue abstracts it.

## Task queue

The `TaskQueue` protocol separates orchestration from execution:

- **Producer side** (invoke_agent): `enqueue()` + `poll_result()`
- **Consumer side** (workers): `claim(pool)` + `complete()` + `fail()`

Workers claim by pool (Prefect model): each worker serves one pool and executes whatever lands in it. Handler lookup is the worker's concern.

The `InMemoryTaskQueue` provides per-pool FIFO queues, O(1) claim, backpressure limits, task cancellation, and memory cleanup. It is production-viable for single-server monolith deployment.

## Server bootstrap

```python
from monet.server import bootstrap

worker_task = await bootstrap(
    catalogue_root=".catalogue",
    enable_tracing=True,
)
```

`bootstrap()` handles the full startup sequence with guaranteed ordering:

1. Configure OpenTelemetry tracing
2. Configure catalogue (from path or `MONET_CATALOGUE_DIR` env var)
3. Create task queue (in-memory by default)
4. Start background worker for the local pool
5. Monitor worker health via done_callback

## Pool system

Agents declare their pool via the `@agent` decorator:

```python
@agent("researcher", pool="local")     # runs in-process
@agent("transcriber", pool="default")  # remote worker
@agent("pipeline", pool="cloud")       # forwarded to Cloud Run/ECS
```

Default pool is `"local"`. The manifest tracks pool assignments. Workers claim tasks from their assigned pool only.

## Pointer-only state

Full artifact content never enters orchestration state. After each wave:

1. `_resolve_wave_result()` extracts a 200-char summary and catalogue pointers
2. Downstream agents receive pointers in their context
3. Agents that need full content call `resolve_context()`:

```python
from monet import resolve_context

@agent("writer")
async def write(task: str, context: list) -> str:
    resolved = await resolve_context(context)
    # resolved entries now have 'content' field populated from catalogue
    ...
```

This keeps LangGraph checkpoints small and LLM context focused.

## Signal routing

The execution graph routes signals via `SignalRouter`:

- `BLOCKING` signals (needs_human_review, escalation_required) → HITL interrupt
- `RECOVERABLE` signals (rate_limited, tool_unavailable, capability_unavailable) → retry wave
- `INFORMATIONAL` signals → feed QA reflection verdict
- `AUDIT` signals → logged, no routing action

## Human-in-the-loop

HITL is the orchestrator's concern, not the agent's:

- **Planning graph**: human approval gate after work brief generation. Bounded revision count (max 3).
- **Execution graph**: HITL interrupt on blocking signals or QA failure. Resume with `Command(resume={"action": "abort"|..., "feedback": str})`.

Agents emit honest signals. The orchestrator decides what action each signal triggers.
