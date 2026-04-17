# Orchestration API Reference

All exports from `monet.orchestration`.

## State types

### `PlanningState`

```python
class PlanningState(TypedDict, total=False):
    task: str
    work_brief_pointer: ArtifactPointer | None
    routing_skeleton: dict[str, Any] | None
    planner_error: str | None
    planning_context: Annotated[list[dict[str, Any]], _append_reducer]
    human_feedback: str | None
    plan_approved: bool | None
    revision_count: int
    trace_id: str
    run_id: str
```

### `ExecutionState`

```python
class ExecutionState(TypedDict, total=False):
    work_brief_pointer: ArtifactPointer
    routing_skeleton: dict[str, Any]
    completed_node_ids: list[str]
    wave_results: Annotated[list[dict[str, Any]], _append_reducer]
    wave_reflections: Annotated[list[dict[str, Any]], _append_reducer]
    signals: SignalsSummary | None
    abort_reason: str | None
    trace_id: str
    run_id: str
    trace_carrier: dict[str, str]
```

### `WaveItem`

```python
class WaveItem(TypedDict, total=False):
    agent_id: str
    command: str
    task: str
    phase_index: int
    wave_index: int
    item_index: int
    trace_id: str
    run_id: str
    context: list[dict[str, Any]]
    trace_carrier: dict[str, str]
```

### `WaveResult`

```python
class WaveResult(TypedDict):
    node_id: str
    agent_id: str
    command: str
    output: str | dict[str, Any] | None
    artifacts: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    success: bool
```

## Functions

### `invoke_agent`

```python
async def invoke_agent(
    agent_id: str,
    command: str = "fast",
    task: str = "",
    context: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    skills: list[str] | None = None,
    **kwargs: Any,
) -> AgentResult
```

Queue-based agent dispatch. Checks the capability manifest before enqueue — returns `CAPABILITY_UNAVAILABLE` signal instantly if the agent is not declared. Looks up pool from manifest, enqueues to the pool's queue, and polls for result. Cancels the task on timeout.

Environment variables:

- `MONET_AGENT_TIMEOUT` — poll timeout in seconds (default 600)

### `configure_queue`

```python
def configure_queue(queue: TaskQueue | None) -> None
```

Set or clear the task queue used by `invoke_agent`. Called by `bootstrap()` or manually in tests.

### `build_planning_subgraph`

```python
def build_planning_subgraph(hooks: GraphHookRegistry | None = None) -> StateGraph
```

Builds the planning subgraph. Planner/plan → human approval gate → work brief pointer + routing skeleton output. Revise-with-feedback loops back to planner (max 3 revision rounds).

### `build_execution_subgraph`

```python
def build_execution_subgraph(hooks: GraphHookRegistry | None = None) -> StateGraph
```

Builds the execution graph. Wave-based parallel execution with QA reflection, retry budget, and signal routing.

## Task Queue Protocol

```python
class TaskQueue(Protocol):
    async def enqueue(self, agent_id, command, ctx, pool="local") -> str: ...
    async def poll_result(self, task_id, timeout) -> AgentResult: ...
    async def claim(self, pool) -> TaskRecord | None: ...
    async def complete(self, task_id, result) -> None: ...
    async def fail(self, task_id, error) -> None: ...
    async def cancel(self, task_id) -> None: ...
```
