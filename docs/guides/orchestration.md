# Orchestration

The orchestration layer integrates monet agents with LangGraph. It provides a two-graph pipeline topology (plus a standalone chat graph), a task queue for decoupled dispatch, and pointer-only state management.

## Topology

`monet run` and chat's `/plan` both drive the compound default graph: `planning → execution`. Triage is a chat-only concern — there is no pipeline entry-time short-circuit. Conversational routing (chat vs planner vs specialist) lives inside `build_chat_graph`.

1. **Planning graph** — planner/plan generates a work brief pointer + routing skeleton. Human approval gate with bounded revision count; revise-with-feedback loops back to planner (max 3 rounds). *Not* invocable via `monet run` — planning is an internal subgraph of the compound default graph.
2. **Execution graph** — wave-based parallel execution via LangGraph `Send`. QA reflection gates. Retry budget. Signal routing. **Invocable** as `monet run --graph execution` with input `{work_brief_pointer, routing_skeleton, run_id, trace_id}`. Scheduled / unattended runs feed a frozen `WorkBrief` pointer (produced by a prior interactive planning session) through this entrypoint. The graph has no HITL approval gate — `--auto-approve` is not needed.

See [Graph Topology](../architecture/graph-topology.md) for the full topology diagrams.

## Plan-freeze workflow for recurring runs

For recurring work, separate **plan iteration** (interactive, HITL) from **plan execution** (unattended, frozen DAG):

1. Iterate the plan interactively in `monet chat` via `/plan → revise → approve`.
2. The approved `WorkBrief` is written to the artifact store by the planning subgraph. The chat transcript prints the `work_brief_pointer`.
3. Fire the frozen DAG directly:

    ```bash
    monet run --graph execution --input '{
      "work_brief_pointer": {"artifact_id": "...", "url": "...", "key": "work_brief"},
      "routing_skeleton": { ... }
    }'
    ```

Each scheduled fire re-queries the world inside every agent invocation — the DAG shape is frozen, agent behaviour is not. See `examples/agent-recruitment/` for a worked example.

## State schemas

Each subgraph has its own TypedDict state:

- `PlanningState` — task, work brief pointer, routing skeleton, planning context, human feedback, revision count
- `ExecutionState` — work brief pointer, routing skeleton, completed node ids, wave results (append-only), wave reflections, signals, abort reason

State is pointer-only: `pending_context` entries contain summaries and artifact store artifact pointers, never full content. Agents that need upstream content call `resolve_context()`.

## Agent invocation

`invoke_agent()` dispatches via the configured task queue:

```python
from monet.orchestration import invoke_agent

result = await invoke_agent("researcher", command="deep", task="quantum computing")
```

The dispatch flow:

1. Emit `agent:started` lifecycle progress event
2. Look up pool from local registry or capability index
3. Enqueue task to the pool's queue
4. Forward worker-side progress events into the LangGraph stream
5. Wait for result (with configurable timeout via `MONET_AGENT_TIMEOUT`)
6. Emit `agent:completed` or `agent:failed` lifecycle progress event
7. Return `AgentResult`

Transport (local call, HTTP, cloud forwarding) is the worker's concern, not the orchestrator's. The queue abstracts it.

## Progress stream convention

Every `invoke_agent` call automatically emits lifecycle events into the LangGraph stream. These bracket agent-authored progress:

```
researcher:deep — agent:started       ← lifecycle (from invoke_agent)
researcher:deep — searching with Exa  ← agent-authored (from emit_progress inside agent)
researcher:deep — synthesising        ← agent-authored
researcher:deep — agent:completed     ← lifecycle (from invoke_agent)
```

Lifecycle status strings use a colon prefix (`agent:started`, `agent:completed`, `agent:failed`) to distinguish them from freeform agent statuses. The convention is universal — any graph that calls `invoke_agent` gets lifecycle framing automatically.

Client-side, lifecycle and agent-authored progress both arrive as `AgentProgress` events with `agent_id`, `command`, `status`, and `run_id` fields. Clients can filter on the `agent:` prefix to separate lifecycle from content.

See the [Custom Graphs guide](custom-graphs.md) for patterns on building graphs with progress streams.

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
    artifacts_root=".artifacts",
    enable_tracing=True,
)
```

`bootstrap()` handles the full startup sequence with guaranteed ordering:

1. Configure OpenTelemetry tracing
2. Configure artifact store (from path or `MONET_ARTIFACTS_DIR` env var)
3. Create task queue (in-memory by default)
4. Start background worker for the local pool
5. Monitor worker health via done_callback

## Pool system

Agents declare their pool via the `@agent` decorator:

```python
@agent("researcher", pool="local")     # runs in the local pool (worker in same process as server)
@agent("transcriber", pool="default")  # remote worker
@agent("pipeline", pool="cloud")       # forwarded to Cloud Run/ECS
```

Default pool is `"local"`. The manifest tracks pool assignments. Workers claim tasks from their assigned pool only.

## Pointer-only state

Full artifact content never enters orchestration state. After each wave:

1. `_resolve_wave_result()` extracts a 200-char summary and artifact store pointers
2. Downstream agents receive pointers in their context
3. Agents that need full content call `resolve_context()`:

```python
from monet import resolve_context

@agent("writer")
async def write(task: str, context: list) -> str:
    resolved = await resolve_context(context)
    # resolved entries now have 'content' field populated from artifact store
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
