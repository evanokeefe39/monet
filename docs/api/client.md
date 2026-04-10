# Client API Reference

## `MonetClient`

```python
from monet.client import MonetClient

class MonetClient:
    def __init__(self, url: str = "http://localhost:2024") -> None
```

High-level typed async client for interacting with a monet server. Manages the three-graph topology (entry, planning, execution) and translates LangGraph state into typed events.

### Run lifecycle

#### `run`

```python
async def run(
    topic: str,
    *,
    run_id: str | None = None,
    auto_approve: bool = False,
) -> AsyncIterator[RunEvent]
```

Start a run and stream typed events. If `auto_approve=True`, planning interrupts are automatically approved. Execution interrupts always pause.

#### `list_runs`

```python
async def list_runs(*, limit: int = 20) -> list[RunSummary]
```

List recent runs with status. Queries server threads tagged as monet entry threads.

#### `get_run`

```python
async def get_run(run_id: str) -> RunDetail
```

Get full run state by inspecting all threads (entry, planning, execution).

### HITL decisions

#### `approve_plan`

```python
async def approve_plan(run_id: str) -> AsyncIterator[RunEvent]
```

Approve a pending plan and continue into execution. Yields remaining run events.

#### `revise_plan`

```python
async def revise_plan(run_id: str, feedback: str) -> AsyncIterator[RunEvent]
```

Send plan back for revision with feedback. May yield another `PlanInterrupt`.

#### `reject_plan`

```python
async def reject_plan(run_id: str) -> None
```

Reject a plan and terminate the run.

#### `retry_wave`

```python
async def retry_wave(run_id: str) -> AsyncIterator[RunEvent]
```

Retry the current wave after an execution interrupt.

#### `abort_run`

```python
async def abort_run(run_id: str) -> None
```

Abort a run during an execution interrupt.

### Results

#### `get_results`

```python
async def get_results(run_id: str) -> RunDetail
```

Alias for `get_run()`. Returns wave results and reflections.

#### `get_artifacts`

```python
async def get_artifacts(run_id: str) -> list[dict[str, Any]]
```

Get all artifact pointers from a run's wave results.

#### `list_pending`

```python
async def list_pending() -> list[PendingDecision]
```

List runs currently waiting for human input.

---

## `run`

```python
from monet import run

async def run(
    topic: str,
    *,
    run_id: str | None = None,
    enable_tracing: bool = False,
) -> AsyncIterator[RunEvent]
```

In-process run without a server. Plans are auto-approved. Yields the same `RunEvent` types as `MonetClient.run()`.

---

## Event types

All events are frozen dataclasses with a `run_id: str` field.

```python
from monet.client import (
    RunEvent,
    TriageComplete,
    PlanReady,
    PlanApproved,
    PlanInterrupt,
    AgentProgress,
    WaveComplete,
    ReflectionComplete,
    ExecutionInterrupt,
    RunComplete,
    RunFailed,
)
```

### `TriageComplete`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `complexity` | `str` | `"simple"`, `"bounded"`, or `"complex"` |
| `suggested_agents` | `list[str]` | Agents suggested by triage |

### `PlanReady`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `goal` | `str` | Plan goal statement |
| `phases` | `list[dict]` | Execution phases |
| `assumptions` | `list[str]` | Planning assumptions |

### `PlanApproved`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |

### `PlanInterrupt`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `brief` | `dict` | Work brief awaiting approval |

Use `approve_plan()`, `revise_plan()`, or `reject_plan()` to continue.

### `AgentProgress`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `agent_id` | `str` | Agent reporting progress |
| `status` | `str` | Progress status message |

### `WaveComplete`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `phase_index` | `int` | Phase number |
| `wave_index` | `int` | Wave number within phase |
| `results` | `list[dict]` | Agent results from this wave |

### `ReflectionComplete`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `verdict` | `str` | QA verdict (`"pass"`, `"retry"`, etc.) |
| `notes` | `str` | QA notes |

### `ExecutionInterrupt`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `reason` | `str` | Why execution paused |
| `phase_index` | `int` | Current phase |
| `wave_index` | `int` | Current wave |

Use `retry_wave()` or `abort_run()` to continue.

### `RunComplete`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `wave_results` | `list[dict]` | All wave results |
| `wave_reflections` | `list[dict]` | All QA reflections |

### `RunFailed`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `error` | `str` | Error message |

### `RunEvent`

Union type of all event types:

```python
RunEvent = (
    TriageComplete | PlanReady | PlanApproved | PlanInterrupt
    | AgentProgress | WaveComplete | ReflectionComplete
    | ExecutionInterrupt | RunComplete | RunFailed
)
```

---

## Query types

### `RunSummary`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `status` | `str` | Current status |
| `phase` | `str` | Current phase |
| `created_at` | `str` | ISO 8601 timestamp |

### `RunDetail`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `status` | `str` | Current status |
| `phase` | `str` | Current phase |
| `triage` | `dict` | Triage output |
| `work_brief` | `dict` | Planning output |
| `wave_results` | `list[dict]` | Execution results |
| `wave_reflections` | `list[dict]` | QA reflections |

### `PendingDecision`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `decision_type` | `str` | `"plan_approval"` or `"execution_review"` |
| `summary` | `str` | Human-readable summary |
| `detail` | `dict` | Decision context |
