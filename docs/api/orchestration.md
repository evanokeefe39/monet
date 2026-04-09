# Orchestration API Reference

All exports from `monet.orchestration`.

## State types

### `EntryState`

```python
class EntryState(TypedDict, total=False):
    task: str
    triage: dict[str, Any] | None
    trace_id: str
    run_id: str
```

### `PlanningState`

```python
class PlanningState(TypedDict, total=False):
    task: str
    work_brief: dict[str, Any] | None
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
    work_brief: dict[str, Any]
    current_phase_index: int
    current_wave_index: int
    wave_results: Annotated[list[dict[str, Any]], _append_reducer]
    wave_reflections: Annotated[list[dict[str, Any]], _append_reducer]
    completed_phases: Annotated[list[int], _int_append_reducer]
    signals: SignalsSummary | None
    abort_reason: str | None
    revision_count: int
    trace_id: str
    run_id: str
    pending_context: list[dict[str, Any]]
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
    phase_index: int
    wave_index: int
    item_index: int
    agent_id: str
    command: str
    output: str | dict[str, Any] | None
    artifacts: list[dict[str, Any]]
    signals: list[dict[str, Any]]
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

### `build_entry_graph`

```python
def build_entry_graph() -> StateGraph
```

Builds the triage graph. Single node: planner/fast classifies complexity.

### `build_planning_graph`

```python
def build_planning_graph() -> StateGraph
```

Builds the planning graph. Planner/plan → human approval gate → work brief output. Max 3 revision rounds.

### `build_execution_graph`

```python
def build_execution_graph() -> StateGraph
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
