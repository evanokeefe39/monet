# Client API Reference

## `MonetClient`

```python
from monet.client import MonetClient

class MonetClient:
    def __init__(
        self,
        url: str | None = None,
        *,
        api_key: str | None = None,
        data_plane_url: str | None = None,
        graph_ids: dict[str, str] | None = None,
    ) -> None
```

Graph-agnostic async client. Drives any graph declared in `monet.toml [entrypoints]`, streams typed core events, and exposes generic HITL resume/abort. Pipeline-specific composition (entry → planning → execution with HITL) lives in [`monet.pipelines.default`](#default-pipeline).

`url` defaults to `MONET_SERVER_URL` (then `http://localhost:2026`). `api_key` defaults to `MONET_API_KEY`. `data_plane_url` defaults to `MONET_DATA_PLANE_URL`; when unset, all requests (including event recording and queries) go to `url`.

In split-plane mode, `resume()` posts a `hitl_decision` event to the data plane before issuing the LangGraph `Command(resume=...)`. The step is idempotent — a 409 from the data plane means the decision was already recorded and the graph command can be retried safely.

### Run lifecycle

#### `run`

```python
async def run(
    graph_id: str,
    input: dict[str, Any] | str | None = None,
    *,
    run_id: str | None = None,
) -> AsyncIterator[RunEvent]
```

Drive one declared graph and stream typed core events. If `input` is a string, it is wrapped by `task_input()`. Raises `GraphNotInvocable` if `graph_id` is not declared in `[entrypoints]`.

Yields: `RunStarted`, then zero-or-more `NodeUpdate` / `AgentProgress` / `SignalEmitted`, then either `Interrupt` (paused) or `RunComplete` / `RunFailed`.

#### `list_runs`

```python
async def list_runs(*, limit: int = 20) -> list[RunSummary]
```

List recent runs, grouped by `monet_run_id` metadata across threads.

#### `get_run`

```python
async def get_run(run_id: str) -> RunDetail
```

Merge all threads for *run_id* into a generic `RunDetail`. Pipeline-specific typed views project from this — e.g. `DefaultPipelineRunDetail.from_run_detail(detail)`.

### HITL

#### `resume`

```python
async def resume(
    run_id: str,
    tag: str,
    payload: dict[str, Any],
) -> None
```

Resume a paused interrupt. Validates the run is paused at *tag* before dispatching.

Raises:

| Exception | When |
|---|---|
| `RunNotInterrupted` | No interrupted thread for `run_id` |
| `AlreadyResolved` | Run already moved past the interrupt |
| `AmbiguousInterrupt` | Multiple pending nodes |
| `InterruptTagMismatch` | `tag` does not match the current interrupt |

All inherit from `MonetClientError`.

#### `abort`

```python
async def abort(run_id: str) -> None
```

Abort a paused run with a canonical `{"action": "abort"}` resume payload.

### Queries

#### `list_pending`

```python
async def list_pending() -> list[PendingDecision]
```

List runs waiting for human input. `decision_type` is the raw interrupt tag (graph node name that called `interrupt()`).

#### `list_graphs`

```python
async def list_graphs() -> list[str]
```

Return graph IDs available on the connected server.

#### `query_events`

```python
async def query_events(
    run_id: str,
    *,
    after: int = 0,
    limit: int = 100,
) -> list[ProgressEvent]
```

Fetch typed progress events for `run_id` from the data plane. Returns events with `event_id > after`, ordered by `event_id`. In split-plane mode reads from `data_plane_url`; otherwise reads from `url`.

#### `subscribe_events`

```python
async def subscribe_events(
    run_id: str,
    *,
    after: int = 0,
) -> AsyncIterator[ProgressEvent]
```

Stream typed progress events for `run_id` from the data plane. Tracks the cursor internally so reconnects never duplicate. Stops when a terminal event (`run_completed` / `run_cancelled`) is received or the caller closes the iterator.

### Chat

Chat graph methods (`create_chat`, `list_chats`, `send_message`, `send_context`, `get_chat_history`, `rename_chat`, `get_most_recent_chat`) resolve the chat graph via `monet.toml [graphs]` role mapping (defaults to `chat`).

---

## Default pipeline

The default multi-graph pipeline (entry → planning → execution with HITL plan approval) is an adapter that composes `MonetClient` calls.

```python
from monet.pipelines.default import run as run_default

async def run(
    client: MonetClient,
    topic: str,
    *,
    run_id: str | None = None,
    auto_approve: bool = False,
) -> AsyncIterator[DefaultPipelineEvent | RunComplete | RunFailed]
```

### Typed HITL verbs

All wrap `client.resume(...)` with typed `DefaultInterruptTag` + `TypedDict` payload:

```python
from monet.pipelines.default import (
    approve_plan,   # resume "human_approval" with {"approved": True}
    revise_plan,    # resume "human_approval" with {"approved": False, "feedback": ...}
    reject_plan,    # resume "human_approval" with {"approved": False, "feedback": None}
    retry_wave,     # resume "human_interrupt" with {"action": None}
    abort_run,      # resume "human_interrupt" with {"action": "abort"}
)
```

### Typed RunDetail view

```python
from monet.pipelines.default import DefaultPipelineRunDetail

view = DefaultPipelineRunDetail.from_run_detail(detail)
view.routing_skeleton
view.work_brief_pointer
view.wave_results
view.wave_reflections
```

---

## Core event types

All events are `@dataclass(frozen=True)` with a `run_id: str` field.

```python
from monet.client import (
    RunEvent,
    RunStarted,
    NodeUpdate,
    AgentProgress,
    SignalEmitted,
    Interrupt,
    RunComplete,
    RunFailed,
)
```

### `RunStarted`

| Field | Type | Description |
|---|---|---|
| `graph_id` | `str` | Graph being driven |
| `thread_id` | `str` | Server-side thread |

### `NodeUpdate`

| Field | Type | Description |
|---|---|---|
| `node` | `str` | Node that wrote the delta |
| `update` | `dict[str, Any]` | State delta |

### `AgentProgress`

| Field | Type | Description |
|---|---|---|
| `agent_id` | `str` | Agent reporting progress |
| `status` | `str` | Progress status — lifecycle (`agent:started`, `agent:completed`, `agent:failed`) or freeform |
| `command` | `str` | Agent command (e.g. `"fast"`, `"deep"`) |
| `reasons` | `str` | Failure explanation (populated on `agent:failed`) |

### `SignalEmitted`

| Field | Type | Description |
|---|---|---|
| `agent_id` | `str` | Agent emitting the signal |
| `signal_type` | `str` | Signal kind (see `monet.signals`) |
| `payload` | `dict[str, Any]` | Signal fields |

### `Interrupt`

| Field | Type | Description |
|---|---|---|
| `tag` | `str` | Interrupt node name |
| `values` | `dict[str, Any]` | kwargs passed to `interrupt()` |
| `next_nodes` | `list[str]` | `state.next` |

### `RunComplete`

| Field | Type | Description |
|---|---|---|
| `final_values` | `dict[str, Any]` | Final state snapshot |

### `RunFailed`

| Field | Type | Description |
|---|---|---|
| `error` | `str` | Error message |

### `RunEvent`

```python
RunEvent = (
    RunStarted | NodeUpdate | AgentProgress | SignalEmitted
    | Interrupt | RunComplete | RunFailed
)
```

---

## Default-pipeline event types

```python
from monet.pipelines.default import (
    DefaultPipelineEvent,
    TriageComplete,
    PlanReady,
    PlanApproved,
    PlanInterrupt,
    WaveComplete,
    ReflectionComplete,
    ExecutionInterrupt,
)
```

### `TriageComplete`

| Field | Type | Description |
|---|---|---|
| `complexity` | `str` | `"simple"`, `"bounded"`, `"complex"` |
| `suggested_agents` | `list[str]` | Triage-suggested agents |

### `PlanReady`

| Field | Type | Description |
|---|---|---|
| `goal` | `str` | Plan goal |
| `nodes` | `list[dict]` | Routing skeleton nodes |

### `PlanInterrupt`

| Field | Type | Description |
|---|---|---|
| `work_brief_pointer` | `ArtifactPointer` | Pointer to the planner's work brief |
| `routing_skeleton` | `dict` | `{goal, nodes}` |

Continue via `approve_plan`, `revise_plan`, or `reject_plan`.

### `WaveComplete`

| Field | Type | Description |
|---|---|---|
| `wave_index` | `int` | Monotonic counter |
| `node_ids` | `list[str]` | Skeleton node ids in this batch |
| `results` | `list[dict]` | Agent results |

### `ReflectionComplete`

| Field | Type | Description |
|---|---|---|
| `verdict` | `str` | QA verdict |
| `notes` | `str` | QA notes |

### `ExecutionInterrupt`

| Field | Type | Description |
|---|---|---|
| `reason` | `str` | Why execution paused |
| `last_result` | `dict` | Last wave result |
| `pending_node_ids` | `list[str]` | Unfinished skeleton nodes |

Continue via `retry_wave` or `abort_run`.

---

## Query types

### `RunSummary`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `status` | `str` | Current status |
| `completed_stages` | `list[str]` | Per-graph stages observed |
| `created_at` | `str` | ISO 8601 timestamp |

### `RunDetail`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `status` | `str` | Current status |
| `completed_stages` | `list[str]` | Per-graph stages observed |
| `values` | `dict[str, Any]` | Merged state from all threads |
| `pending_interrupt` | `Interrupt \| None` | Current pause |

### `PendingDecision`

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Run identifier |
| `decision_type` | `str` | Raw interrupt tag (graph node name) |
| `summary` | `str` | Optional human-readable summary |
| `detail` | `dict` | Optional context |

---

## Exceptions

```python
from monet.client import (
    MonetClientError,
    RunNotInterrupted,
    AlreadyResolved,
    AmbiguousInterrupt,
    InterruptTagMismatch,
    GraphNotInvocable,
)
```

All inherit from `MonetClientError`. They are caller errors, not graph errors — graph-level failures surface as `RunFailed` events.
