# Revised Plan: Architectural Cleanup

Pointer-Only Planning, Typed Result Contract, Queue Subpackage,
ManifestHandle, and Progress Streaming.

Supersedes the prior plan. Incorporates panel review feedback and
author corrections on registry wiring, async primitives, checkpoint
compatibility, and failure path observability.

---

## Ordering Rationale

Queue restructuring first (all subsequent work targets new import
paths). Then types and state schema changes (WorkBrief, ArtifactPointer,
dead type removal). Then manifest handle (worker-side wiring). Then
planning graph changes (pointer-only, planning_failed). Then execution
graph and _run.py changes (with checkpoint compatibility shim). Then
progress streaming. Then orchestrator guard removal. Tests are required
per step, not batched at the end.

---

## Step 1: Queue Subpackage Restructuring

Move flat files into a proper subpackage mirroring the catalogue
pattern. Flattened structure (no tasks/ subdirectory, no factory
module):

```
src/monet/
    queue/
        __init__.py          # Re-exports everything queue.py exports today
        _interface.py        # TaskQueue Protocol, TaskStatus, TaskRecord
        _worker.py           # run_worker() from core/queue_worker.py
        backends/
            __init__.py      # Empty, namespace only
            memory.py        # InMemoryTaskQueue
            redis.py         # RedisTaskQueue
            upstash.py       # UpstashTaskQueue
            sqlite.py        # SQLiteTaskQueue
```

`queue/__init__.py` re-exports every symbol currently exported from
the old `queue.py`. This is not a compatibility shim -- it is the
permanent public surface:

```python
from monet.queue._interface import TaskQueue, TaskRecord, TaskStatus

from monet.queue._worker import run_worker

def __getattr__(name: str) -> Any:
    if name == "InMemoryTaskQueue":
        from monet.queue.backends.memory import InMemoryTaskQueue
        return InMemoryTaskQueue
    if name == "SQLiteTaskQueue":
        from monet.queue.backends.sqlite import SQLiteTaskQueue
        return SQLiteTaskQueue
    if name == "RedisTaskQueue":
        from monet.queue.backends.redis import RedisTaskQueue
        return RedisTaskQueue
    if name == "UpstashTaskQueue":
        from monet.queue.backends.upstash import UpstashTaskQueue
        return UpstashTaskQueue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "InMemoryTaskQueue",
    "RedisTaskQueue",
    "SQLiteTaskQueue",
    "TaskQueue",
    "TaskRecord",
    "TaskStatus",
    "UpstashTaskQueue",
    "run_worker",
]
```

Delete after confirming no remaining imports:
- `src/monet/queue.py`
- `src/monet/core/queue_memory.py`
- `src/monet/core/queue_redis.py`
- `src/monet/core/queue_sqlite.py`
- `src/monet/core/queue_upstash.py`
- `src/monet/core/queue_worker.py`

Import sites to update (13 in src/, 8 in tests/):

src/:
- `cli/_worker.py` -- `from monet.core.queue_worker import run_worker`
- `core/queue_memory.py` -- self (deleted, moved)
- `core/queue_redis.py` -- self (deleted, moved)
- `core/queue_sqlite.py` -- self (deleted, moved)
- `core/queue_upstash.py` -- self (deleted, moved)
- `core/queue_worker.py` -- self (deleted, moved)
- `core/worker_client.py` -- `from monet.queue import TaskRecord`
- `orchestration/_invoke.py` -- `from monet.queue import TaskQueue`
- `queue.py` -- self (deleted, replaced by package)
- `server/__init__.py` -- `from monet.queue import TaskQueue`
- `server/_bootstrap.py` -- `from monet.core.queue_memory import InMemoryTaskQueue`, `from monet.core.queue_worker import run_worker`
- `server/_routes.py` -- `from monet.queue import TaskQueue`
- `server/default_graphs.py` -- check for queue imports

tests/:
- `conftest.py` -- `from monet.core.queue_memory import InMemoryTaskQueue`, `from monet.core.queue_worker import run_worker`
- `test_public_api.py`
- `test_queue.py`
- `test_queue_corrupted_data.py`
- `test_queue_redis.py`
- `test_queue_sqlite.py`
- `test_queue_upstash.py`
- `test_server_routes.py`

All updated imports use the public surface (`from monet.queue import ...`).
No consumer imports from `monet.queue.backends.*` directly except tests
that instantiate specific backends.

**Verification gate:** `uv run pytest && uv run ruff check . && uv run mypy src/` must pass before proceeding.

---

## Step 2: ArtifactPointer Key Field and find_artifact

File: `src/monet/types.py`

Two-class pattern (committed, not optional -- mypy strict requires it):

```python
class _ArtifactPointerRequired(TypedDict):
    """Required fields for ArtifactPointer."""
    artifact_id: str
    url: str

class ArtifactPointer(_ArtifactPointerRequired, total=False):
    """Reference to an artifact in the catalogue.

    ``key`` is an optional semantic tag used to identify artifacts by
    purpose rather than position. Set by the agent at write time,
    consumed by ``find_artifact()`` at lookup time.
    """
    key: str
```

Add lookup helper in `src/monet/types.py`:

```python
def find_artifact(
    artifacts: tuple[ArtifactPointer, ...], key: str
) -> ArtifactPointer | None:
    """Find the first artifact matching a semantic key, or None."""
    return next((a for a in artifacts if a.get("key") == key), None)
```

Export `find_artifact` from `src/monet/__init__.py` and add to `__all__`.

Update `CatalogueHandle.write()` in `src/monet/core/catalogue.py`:

```python
async def write(
    self,
    content: bytes,
    content_type: str,
    summary: str,
    confidence: float,
    completeness: str,
    sensitivity_label: str = "internal",
    key: str | None = None,
    **kwargs: Any,
) -> ArtifactPointer:
```

When `key` is provided, include it in the returned pointer. The
catalogue backend does not need to know about keys -- they live
on the pointer only.

Update `write_artifact()` in `src/monet/core/stubs.py` to accept
optional `key: str | None = None` and pass through to
`get_catalogue().write()`.

Update `_routes.py` `complete_task` to include `key` when constructing
`ArtifactPointer` from request body:

```python
ArtifactPointer(
    artifact_id=a.get("artifact_id", ""),
    url=a.get("url", ""),
    **({"key": a["key"]} if "key" in a else {}),
)
```

Tests required:
- `find_artifact` with: no artifacts, no match, single match,
  multiple artifacts (first match wins), artifact without key field
- `CatalogueHandle.write()` with and without key
- `write_artifact()` passthrough

**Verification gate:** `uv run pytest && uv run mypy src/`

---

## Step 3: ManifestHandle -- Worker-Side Registry Access

The manifest/capability registry is a worker-side concern. The planner
is an agent that runs on a worker. It needs `get_manifest().capabilities()`
to enumerate available agents for roster building. The orchestrator
does not consult the registry directly.

No protocol. No `CapabilityRegistryClient`. Wrap the existing
`AgentManifest` in the same handle pattern as `CatalogueHandle`.

New file: `src/monet/core/manifest_handle.py`

```python
"""Manifest handle -- worker-side access to agent capability registry.

get_manifest() is one of the core SDK getters alongside get_catalogue()
and get_run_context(). In monolith mode, the backend is default_manifest.
In distributed mode, workers configure their own manifest from server
registration responses.

The orchestrator does not call get_manifest(). Pool routing in
invoke_agent uses the manifest as a convenience lookup, not a
correctness dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.core.manifest import AgentCapability, AgentManifest

_backend: AgentManifest | None = None


def _set_manifest_backend(manifest: AgentManifest | None) -> None:
    global _backend
    _backend = manifest


class ManifestHandle:
    """Returned by get_manifest(). Provides read access to capabilities.

    Reads _backend from the module global on every call so
    configure_manifest() takes effect immediately.
    """

    def capabilities(self) -> list[AgentCapability]:
        if _backend is None:
            raise RuntimeError(
                "get_manifest() requires a backend. "
                "Call configure_manifest() at startup."
            )
        return _backend.capabilities()

    def is_available(self, agent_id: str, command: str) -> bool:
        return _backend.is_available(agent_id, command) if _backend else False

    def get_pool(self, agent_id: str, command: str) -> str | None:
        return _backend.get_pool(agent_id, command) if _backend else None


_handle = ManifestHandle()


def get_manifest() -> ManifestHandle:
    """Return the manifest handle.

    One of the core SDK getters alongside get_catalogue() and
    get_run_context().
    """
    return _handle
```

New public configuration surface in `src/monet/manifest.py` (mirrors
`src/monet/catalogue/__init__.py` pattern):

```python
"""Manifest configuration -- call once at startup."""

from monet.core.manifest import AgentManifest
from monet.core.manifest_handle import _set_manifest_backend


def configure_manifest(manifest: AgentManifest | None) -> None:
    """Configure the manifest backend.

    Monolith mode:
        from monet.core.manifest import default_manifest
        configure_manifest(default_manifest)

    Distributed mode: workers build a manifest from server
    registration responses.
    """
    _set_manifest_backend(manifest)
```

Wire in `src/monet/server/_bootstrap.py` -- add after existing manifest
declarations:

```python
from monet.manifest import configure_manifest
from monet.core.manifest import default_manifest
configure_manifest(default_manifest)
```

Export `get_manifest` from `src/monet/__init__.py`.

Update `_validate.py` to use `get_manifest()` instead of `default_manifest`:

```python
from monet.core.manifest_handle import get_manifest

def _assert_registered(agent_id: str, command: str) -> None:
    if not get_manifest().is_available(agent_id, command):
        ...
```

Tests required:
- `ManifestHandle` with no backend configured (raises RuntimeError)
- `ManifestHandle` with `default_manifest` configured (returns capabilities)
- `configure_manifest(None)` resets

**Verification gate:** `uv run pytest && uv run mypy src/`

---

## Step 4: WorkBrief Typed, Dead Type Removal, PlanningState Update

File: `src/monet/orchestration/_state.py`

### 4a. WorkBrief two-class pattern

```python
class WaveItemSpec(TypedDict):
    agent_id: str
    command: str
    task: str

class Wave(TypedDict):
    items: list[WaveItemSpec]

class Phase(TypedDict):
    name: str
    waves: list[Wave]

class _WorkBriefRequired(TypedDict):
    goal: str
    phases: list[Phase]

class WorkBrief(_WorkBriefRequired, total=False):
    assumptions: list[str]
    is_sensitive: bool
```

### 4b. PlanningState with work_brief_pointer

Replace `work_brief: dict[str, Any] | None` with
`work_brief_pointer: ArtifactPointer | None`. No discriminated union.
The pointer being None means planning failed. The pointer being
present means planning produced a brief. `route_from_planner` branches
on this directly.

Add `planner_error: str | None` for diagnostic reporting in the
`planning_failed` terminal node. This is not a discriminated union --
it is a separate diagnostic field that is only read in the failure
path. It does not participate in routing.

```python
class PlanningState(TypedDict, total=False):
    """State for the planning graph with HITL approval loop."""

    task: str
    work_brief_pointer: ArtifactPointer | None
    planner_error: str | None
    planning_context: Annotated[list[dict[str, Any]], _append_reducer]
    human_feedback: str | None
    plan_approved: bool | None
    revision_count: int
    trace_id: str
    run_id: str
```

### 4c. ExecutionState

Add `work_brief_pointer: ArtifactPointer` as a non-optional field
(execution cannot begin without a resolved plan). Keep
`work_brief: dict[str, Any]` for the hydrated brief loaded by
`load_plan`.

### 4d. Dead type removal

Delete `AgentStateEntry` and `GraphState` from `_state.py`. Grep
codebase to confirm no consumers.

Tests required:
- Verify `WorkBrief` type checks with mypy (goal and phases required,
  assumptions and is_sensitive optional)
- Existing tests that set `work_brief` in PlanningState must update
  to `work_brief_pointer`

**Verification gate:** `uv run mypy src/`

---

## Step 5: Planner Agent -- Validate, Keyed Write, Cross-Check

File: `src/monet/agents/planner/__init__.py`

### 5a. Replace default_manifest with get_manifest

Replace both `default_manifest.capabilities()` calls (lines 53-58
and 82-88) with `get_manifest().capabilities()`.

Extract the duplicated roster-building logic into `_build_roster()`:

```python
from monet.core.manifest_handle import get_manifest

_PLANNER_EXCLUDE: tuple[str, ...] = ("planner",)

def _build_roster() -> list[AgentCapability]:
    return sorted(
        (
            cap
            for cap in get_manifest().capabilities()
            if cap["agent_id"] not in _PLANNER_EXCLUDE
        ),
        key=lambda c: (c["agent_id"], c["command"]),
    )
```

### 5b. Validate and write keyed artifact in planner_plan

The planner must validate its own output before writing. Validation:
`goal` is a non-empty string, `phases` is a non-empty list where
each entry has `name` (str) and `waves` (list with at least one
entry). A malformed brief raises before write -- the error surfaces
as a task failure.

After validation, write with `key="work_brief"`:

```python
from monet import get_catalogue

async def _validate_brief(brief: dict[str, Any]) -> None:
    """Raise ValueError if the brief is structurally invalid."""
    if not isinstance(brief.get("goal"), str) or not brief["goal"].strip():
        raise ValueError("Work brief missing or empty 'goal'")
    phases = brief.get("phases")
    if not isinstance(phases, list) or len(phases) == 0:
        raise ValueError("Work brief has no phases")
    for i, phase in enumerate(phases):
        if not isinstance(phase.get("name"), str):
            raise ValueError(f"Phase {i} missing 'name'")
        waves = phase.get("waves")
        if not isinstance(waves, list) or len(waves) == 0:
            raise ValueError(f"Phase {i} has no waves")

# In planner_plan, after parsing:
await _validate_brief(brief)

pointer = await get_catalogue().write(
    content=json.dumps(brief).encode(),
    content_type="application/json",
    summary=f"Work brief: {brief['goal'][:100]}",
    confidence=1.0,
    completeness="complete",
    key="work_brief",
)
```

### 5c. Include pointer in result output for cross-checking

The planner returns JSON that includes the pointer artifact_id so
`planner_node` can cross-check `find_artifact` against what the
planner itself reported:

```python
return json.dumps(
    {"work_brief_artifact_id": pointer["artifact_id"]},
    separators=(",", ":"),
)
```

This means `planner_node` can verify that
`find_artifact(result.artifacts, "work_brief")` returned a pointer
whose `artifact_id` matches the one the planner reported in its
output. If they disagree, the error message names both IDs.

Remove `from monet.core.manifest import default_manifest` import.

Tests required:
- `_validate_brief` with valid brief, missing goal, empty phases,
  phase missing name, phase with no waves
- `_build_roster` filtering (excludes "planner")
- Planner writes keyed artifact and includes pointer in output

---

## Step 6: planner_node -- Pointer-Only, planning_failed Terminal

File: `src/monet/orchestration/planning_graph.py`

### 6a. planner_node returns pointer, not content

Remove all `get_catalogue` imports. Remove all JSON parsing of brief
content. The planning graph never inspects brief content.

```python
from monet.types import find_artifact

async def planner_node(
    state: PlanningState, config: RunnableConfig
) -> dict[str, Any]:
    """Call planner/plan to produce a work brief."""
    context_entries: list[dict[str, Any]] = []
    for entry in state.get("planning_context") or []:
        context_entries.append(
            {
                "type": "artifact",
                "summary": entry.get("content", ""),
                "content": entry.get("content", ""),
            }
        )
    feedback = state.get("human_feedback")
    if feedback:
        context_entries.append(
            {"type": "instruction", "summary": "Human feedback",
             "content": feedback}
        )

    async with attached_trace(extract_carrier_from_config(config)):
        result = await invoke_agent(
            "planner",
            command="plan",
            task=state["task"],
            context=context_entries,
            trace_id=state.get("trace_id", ""),
            run_id=state.get("run_id", ""),
        )

    if not result.success:
        reasons = "; ".join(
            (s.get("reason") or "").splitlines()[0][:200]
            for s in result.signals
            if s.get("reason")
        )
        return {
            "work_brief_pointer": None,
            "planner_error": (
                f"Planner failed: {reasons}" if reasons
                else "Planner failed"
            ),
        }

    pointer = find_artifact(result.artifacts, "work_brief")
    if pointer is None:
        return {
            "work_brief_pointer": None,
            "planner_error": (
                f"Planner did not produce a work_brief artifact. "
                f"{len(result.artifacts)} artifact(s) returned."
            ),
        }

    # Cross-check: if the planner reported an artifact_id in its
    # output, verify it matches the keyed artifact.
    if isinstance(result.output, dict):
        reported_id = result.output.get("work_brief_artifact_id")
        if reported_id and reported_id != pointer["artifact_id"]:
            return {
                "work_brief_pointer": None,
                "planner_error": (
                    f"Planner output artifact_id '{reported_id}' does "
                    f"not match keyed artifact "
                    f"'{pointer['artifact_id']}'."
                ),
            }

    return {"work_brief_pointer": pointer, "planner_error": None}
```

### 6b. route_from_planner -- exhaustive, no silent END

```python
def route_from_planner(state: PlanningState) -> str:
    pointer = state.get("work_brief_pointer")
    if pointer is None:
        return "planning_failed"
    return "human_approval"
```

### 6c. planning_failed terminal node with OTel span

```python
from opentelemetry import trace

_planning_tracer = trace.get_tracer("monet.orchestration.planning")

async def planning_failed_node(
    state: PlanningState,
) -> dict[str, Any]:
    """Terminal node for planning failures. Emits OTel span."""
    error = state.get("planner_error", "Unknown planning failure")
    with _planning_tracer.start_as_current_span(
        "planning.failed",
        attributes={
            "monet.run_id": state.get("run_id", ""),
            "monet.error": error[:500],
        },
    ):
        pass
    return {"plan_approved": False}
```

### 6d. human_approval_node passes pointer

```python
async def human_approval_node(
    state: PlanningState,
) -> dict[str, Any]:
    pointer = state.get("work_brief_pointer")
    if pointer is None:
        return {"plan_approved": False}
    decision = interrupt({"work_brief_pointer": pointer})
    # ... approval logic unchanged ...
```

### 6e. Graph wiring

```python
graph.add_node("planning_failed", planning_failed_node)
graph.add_conditional_edges(
    "planner",
    route_from_planner,
    {
        "human_approval": "human_approval",
        "planning_failed": "planning_failed",
    },
)
graph.add_edge("planning_failed", END)
```

Tests required (parametrized test for route_from_planner covering
all branches):

- `work_brief_pointer` is None, `planner_error` is set
  -> "planning_failed"
- `work_brief_pointer` is None, `planner_error` is None
  -> "planning_failed"
- `work_brief_pointer` is present, `plan_approved` is False
  -> "human_approval"
- `work_brief_pointer` is present, `plan_approved` is True
  -> "human_approval"
- `planning_failed_node` emits OTel span (verify via in-memory
  exporter)
- Cross-check: planner reports artifact_id X, find_artifact returns
  artifact_id Y -> planner_node returns failure

---

## Step 7: load_plan -- Documented Exception, Remove Pre-Check

File: `src/monet/orchestration/execution_graph.py`

### 7a. load_plan reads from catalogue (documented exception)

```python
async def load_plan(
    state: ExecutionState, config: RunnableConfig
) -> dict[str, Any]:
    # DOCUMENTED EXCEPTION: load_plan is the only node in the
    # orchestration layer permitted to read catalogue content.
    # It resolves the work_brief_pointer set during planning
    # into a WorkBrief for use by all subsequent execution
    # nodes. No other orchestration node may call
    # get_catalogue().read().

    pointer = state.get("work_brief_pointer")
    work_brief = state.get("work_brief")

    if pointer:
        # New path: resolve pointer
        from monet import get_catalogue
        content_bytes, _meta = await get_catalogue().read(
            pointer["artifact_id"]
        )
        work_brief = json.loads(content_bytes.decode())
    elif work_brief:
        # COMPATIBILITY SHIM: in-flight runs from before the
        # pointer migration have work_brief set directly in
        # state. Use it as-is.
        # Remove this branch when no in-flight runs remain on
        # the old schema. Condition: all runs created before
        # deployment have completed or been abandoned.
        pass
    else:
        return {
            "abort_reason": (
                "No work_brief_pointer or work_brief in "
                "execution state."
            )
        }

    # ... rest unchanged (tracer setup, carrier injection) ...

    return {
        "work_brief": work_brief,
        "current_phase_index": 0,
        # ... other init fields ...
    }
```

### 7b. Remove prepare_wave manifest pre-check

Delete the `default_manifest.is_available()` check in `prepare_wave`
(lines 149-166). The queue is the coordination mechanism. If an
agent is unavailable, `invoke_agent` returns
`CAPABILITY_UNAVAILABLE` via the signal system. In distributed
mode, workers that lack capability requeue with backoff (follow-on
task, not this plan).

Keep the `IndexError`/`KeyError` guard for invalid phase/wave
indices -- that is structural validation, not capability validation.

Remove `from monet.core.manifest import default_manifest` from
`execution_graph.py`.

Tests required:
- `load_plan` with valid pointer -> hydrates work_brief
- `load_plan` with missing pointer but existing work_brief
  -> uses work_brief (compat shim)
- `load_plan` with neither -> sets abort_reason
- `prepare_wave` no longer checks manifest

---

## Step 8: _run.py -- State Handoff

File: `src/monet/_run.py`

### 8a. Extract pointer from planning state

```python
# After planning completes:
pointer = planning_state.get("work_brief_pointer")
if not pointer:
    yield RunFailed(
        run_id=rid,
        error="Planning produced no work brief pointer",
    )
    return

yield PlanApproved(run_id=rid)

# For PlanReady event: read brief content for the client event.
# This is the CLI/client layer, not orchestration -- reading
# content here is correct.
from monet import get_catalogue
content_bytes, _meta = await get_catalogue().read(
    pointer["artifact_id"]
)
brief = json.loads(content_bytes.decode())

yield PlanReady(
    run_id=rid,
    goal=brief.get("goal", ""),
    phases=brief.get("phases") or [],
    assumptions=brief.get("assumptions") or [],
)

# Pass pointer to execution, not content:
exec_state = (
    await build_execution_graph()
    .compile(checkpointer=ck)
    .ainvoke(
        {
            "work_brief_pointer": pointer,
            "trace_id": rid,
            "run_id": rid,
            "wave_results": [],
            "wave_reflections": [],
            "completed_phases": [],
            "revision_count": 0,
        },
        config={
            "configurable": {"thread_id": f"{rid}-exec"}
        },
    )
)
```

---

## Step 9: Progress Streaming

### 9a. Protocol methods

File: `src/monet/queue/_interface.py`

Add to TaskQueue protocol:

```python
async def publish_progress(
    self, task_id: str, data: dict[str, Any]
) -> None:
    """Publish a progress event for a running task.

    Best-effort. Failures are logged at debug level.
    """
    ...

async def subscribe_progress(
    self, task_id: str
) -> AsyncIterator[dict[str, Any]]:
    """Yield progress events until the task completes.

    Server-side only. RemoteQueue raises NotImplementedError.
    """
    ...
```

### 9b. Bounded internal queue in worker

File: `src/monet/queue/_worker.py`

The `emit_progress` SDK function is synchronous. The worker bridges
this by using a bounded `asyncio.Queue` that decouples the sync
`emit_progress` call from the async `publish_progress` transport.
The publisher puts onto the queue non-blocking and drops on
backpressure. A drain coroutine runs concurrently and flushes
events via `publish_progress`.

```python
import logging

_progress_log = logging.getLogger("monet.worker.progress")

_PROGRESS_QUEUE_SIZE = 64

async def _drain_progress(
    progress_q: asyncio.Queue[dict[str, Any]],
    task_queue: TaskQueue,
    task_id: str,
) -> None:
    """Drain progress events from bounded queue to transport."""
    while True:
        try:
            data = await progress_q.get()
        except asyncio.CancelledError:
            # Drain remaining items on shutdown
            while not progress_q.empty():
                try:
                    data = progress_q.get_nowait()
                    await task_queue.publish_progress(task_id, data)
                except Exception:
                    _progress_log.debug(
                        "Failed to flush progress on shutdown "
                        "for task %s",
                        task_id,
                        exc_info=True,
                    )
            raise
        try:
            await task_queue.publish_progress(task_id, data)
        except Exception:
            _progress_log.debug(
                "Failed to publish progress for task %s",
                task_id,
                exc_info=True,
            )
```

In `_execute()`, set up the progress bridge:

```python
from monet.core.stubs import _progress_publisher

async def _execute(record: TaskRecord) -> None:
    task_id = record["task_id"]
    # ... existing span setup ...

    progress_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
        maxsize=_PROGRESS_QUEUE_SIZE
    )
    drain_task = asyncio.create_task(
        _drain_progress(progress_q, queue, task_id)
    )

    def _publisher(data: dict[str, Any]) -> None:
        try:
            progress_q.put_nowait(data)
        except asyncio.QueueFull:
            _progress_log.debug(
                "Progress queue full for task %s, dropping",
                task_id,
            )

    token = _progress_publisher.set(_publisher)
    try:
        result = await handler(record["context"])
        await queue.complete(task_id, result)
    except Exception as exc:
        # ... existing error handling ...
    finally:
        _progress_publisher.reset(token)
        drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain_task
```

### 9c. _progress_publisher ContextVar in stubs.py

File: `src/monet/core/stubs.py`

```python
_progress_publisher: ContextVar[
    Callable[[dict[str, Any]], None] | None
] = ContextVar("_progress_publisher", default=None)

def emit_progress(data: dict[str, Any]) -> None:
    """Emit a progress event.

    When running inside a worker (outside LangGraph context),
    uses the bounded progress queue set by the worker. When
    running inside LangGraph context (graph nodes), uses
    StreamWriter. Falls back to no-op if neither is available.
    """
    publisher = _progress_publisher.get()
    if publisher is not None:
        publisher(data)
        return
    try:
        from langgraph.config import get_stream_writer
        writer = get_stream_writer()
        writer(data)
    except (LookupError, RuntimeError):
        pass
```

### 9d. Server-side forwarding

File: `src/monet/orchestration/_invoke.py`

```python
import logging

_progress_log = logging.getLogger(
    "monet.orchestration.progress"
)

async def _forward_progress(
    queue: TaskQueue, task_id: str
) -> None:
    """Forward progress events from queue to LangGraph stream."""
    try:
        async for event in queue.subscribe_progress(task_id):
            emit_progress(event)
    except NotImplementedError:
        pass  # RemoteQueue -- expected, not an error
    except asyncio.CancelledError:
        raise
    except Exception:
        _progress_log.debug(
            "Progress forwarding ended for task %s",
            task_id,
            exc_info=True,
        )

async def invoke_agent(...):
    # ... enqueue ...
    progress_task = asyncio.create_task(
        _forward_progress(_task_queue, task_id)
    )
    try:
        result = await _task_queue.poll_result(
            task_id, timeout=_get_timeout()
        )
    finally:
        progress_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await progress_task
```

### 9e. Backend implementations

**InMemory** (`queue/backends/memory.py`):

Per-task subscriber set with bounded asyncio.Queue per subscriber.
Explicit cleanup in `finally` block of the async generator.

```python
# In __init__:
self._progress_subscribers: dict[
    str, set[asyncio.Queue[dict[str, Any]]]
] = defaultdict(set)

async def publish_progress(
    self, task_id: str, data: dict[str, Any]
) -> None:
    for sub_q in self._progress_subscribers.get(task_id, set()):
        try:
            sub_q.put_nowait(data)
        except asyncio.QueueFull:
            pass  # Subscriber too slow, drop event

async def subscribe_progress(
    self, task_id: str
) -> AsyncIterator[dict[str, Any]]:
    sub_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
        maxsize=64
    )
    self._progress_subscribers[task_id].add(sub_q)
    try:
        while True:
            record = self._tasks.get(task_id)
            if record and record["status"] in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                while not sub_q.empty():
                    yield sub_q.get_nowait()
                return
            try:
                data = await asyncio.wait_for(
                    sub_q.get(), timeout=1.0
                )
                yield data
            except TimeoutError:
                continue
    finally:
        self._progress_subscribers[task_id].discard(sub_q)
        if not self._progress_subscribers[task_id]:
            del self._progress_subscribers[task_id]
```

**Redis** (`queue/backends/redis.py`):

```python
async def publish_progress(
    self, task_id: str, data: dict[str, Any]
) -> None:
    client = await self._ensure_client()
    channel = f"{self._prefix}:progress:{task_id}"
    await client.publish(channel, json.dumps(data))

async def subscribe_progress(
    self, task_id: str
) -> AsyncIterator[dict[str, Any]]:
    client = await self._ensure_client()
    pubsub = client.pubsub()
    channel = f"{self._prefix}:progress:{task_id}"
    try:
        await pubsub.subscribe(channel)
        while True:
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if msg is not None and msg["type"] == "message":
                yield json.loads(msg["data"])
            status = await client.hget(
                self._task_key(task_id), "status"
            )
            s = (
                status.decode()
                if isinstance(status, bytes)
                else status
            )
            if s in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                return
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
```

**Upstash** (`queue/backends/upstash.py`):

Progress lists get the same TTL as task keys. No unbounded growth.

```python
async def publish_progress(
    self, task_id: str, data: dict[str, Any]
) -> None:
    key = f"{self._prefix}:progress:{task_id}"
    await self._redis.rpush(key, json.dumps(data))
    await self._redis.expire(key, self._task_ttl)

async def subscribe_progress(
    self, task_id: str
) -> AsyncIterator[dict[str, Any]]:
    from monet.queue import TaskStatus

    key = f"{self._prefix}:progress:{task_id}"
    cursor = 0
    while True:
        items = await self._redis.lrange(
            key, cursor, cursor + 9
        )
        for item in items:
            yield json.loads(item)
            cursor += 1
        if not items:
            status_raw = await self._redis.hget(
                self._task_key(task_id), "status"
            )
            if status_raw and TaskStatus(status_raw) in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                return
            await asyncio.sleep(self._poll_interval)
```

**SQLite** (`queue/backends/sqlite.py`):

Same callback pattern as InMemory (in-process only).

**RemoteQueue** (`core/worker_client.py`):

```python
async def publish_progress(
    self, task_id: str, data: dict[str, Any]
) -> None:
    """POST progress to server."""
    try:
        resp = await self._client.post(
            f"/tasks/{task_id}/progress",
            json=data,
        )
        resp.raise_for_status()
    except Exception:
        _log.debug(
            "Failed to POST progress for task %s",
            task_id,
            exc_info=True,
        )

async def subscribe_progress(
    self, task_id: str
) -> AsyncIterator[dict[str, Any]]:
    raise NotImplementedError(
        "subscribe_progress is not supported on RemoteQueue. "
        "Progress flows via POST "
        "/api/v1/tasks/{task_id}/progress from the worker."
    )
```

Add `POST /api/v1/tasks/{task_id}/progress` route in
`src/monet/server/_routes.py`:

```python
class TaskProgressRequest(BaseModel):
    data: dict[str, Any]

@router.post(
    "/tasks/{task_id}/progress",
    dependencies=[Depends(require_api_key)],
)
async def task_progress(
    task_id: str,
    body: TaskProgressRequest,
    queue: Queue,
) -> dict[str, str]:
    """Receive a progress event from a remote worker."""
    await queue.publish_progress(task_id, body.data)
    return {"status": "ok"}
```

Tests required:
- Progress round-trip with InMemoryTaskQueue: agent calls
  emit_progress -> publish_progress -> subscribe_progress ->
  forward to StreamWriter
- Publisher drops events on queue full (verify no exception,
  verify debug log)
- Subscriber cleanup: subscriber queue removed after iteration
- Drain task flushes remaining events on cancellation
- RemoteQueue.subscribe_progress raises NotImplementedError

---

## Step 10: Remove Orchestrator Manifest Guards

File: `src/monet/orchestration/_invoke.py`

Remove the `default_manifest.is_available()` guard (lines 104-125).
Replace pool routing (line 136) with `get_manifest().get_pool()`:

```python
from monet.core.manifest_handle import get_manifest

# In invoke_agent, after queue check:
pool = get_manifest().get_pool(agent_id, command) or "local"
task_id = await _task_queue.enqueue(
    agent_id, command, ctx, pool=pool
)
```

The manifest guard is removed entirely. If an agent is not in the
manifest, `get_pool()` returns None and the task routes to the
"local" pool. The worker either handles it (has the handler) or
the task fails at execution time.

Remove `from monet.core.manifest import default_manifest` from
`_invoke.py`.

Follow-on task (not this plan): requeue-with-backoff mechanism.
Workers that lack capability for a claimed task requeue it with
backoff delay. Requires per-backend: visibility timeout or
requeue-after delay, max-requeue count, dead-letter path surfacing
CAPABILITY_UNAVAILABLE to the orchestrator. Track separately.

---

## Step 11: Catalogue Decoupling in Bootstrap

File: `src/monet/server/_bootstrap.py`

Make catalogue conditional on deployment mode. In monolith mode
the server configures catalogue because the in-process worker
shares the process. In distributed mode, workers configure their
own catalogue independently.

```python
distributed_mode = os.environ.get(
    "MONET_DISTRIBUTED", ""
).lower() in ("1", "true")

if not distributed_mode:
    # Monolith: server configures catalogue; in-process worker
    # inherits.
    from monet.catalogue import catalogue_from_env
    from monet.catalogue import configure_catalogue
    root = Path(catalogue_root) if catalogue_root else None
    service = catalogue_from_env(default_root=root)
    configure_catalogue(service)
# Distributed: each worker calls configure_catalogue() on
# startup.
```

---

## Files Modified

| File | Change |
|---|---|
| `src/monet/queue.py` | Delete (replaced by package) |
| `src/monet/core/queue_memory.py` | Delete (moved) |
| `src/monet/core/queue_redis.py` | Delete (moved) |
| `src/monet/core/queue_sqlite.py` | Delete (moved) |
| `src/monet/core/queue_upstash.py` | Delete (moved) |
| `src/monet/core/queue_worker.py` | Delete (moved) |
| `src/monet/queue/__init__.py` | New -- public surface re-exports |
| `src/monet/queue/_interface.py` | New -- TaskQueue protocol + progress |
| `src/monet/queue/_worker.py` | New -- run_worker with progress bridge |
| `src/monet/queue/backends/__init__.py` | New -- empty namespace |
| `src/monet/queue/backends/memory.py` | New -- InMemoryTaskQueue + progress |
| `src/monet/queue/backends/redis.py` | New -- RedisTaskQueue + progress |
| `src/monet/queue/backends/upstash.py` | New -- UpstashTaskQueue + progress |
| `src/monet/queue/backends/sqlite.py` | New -- SQLiteTaskQueue + progress |
| `src/monet/types.py` | Two-class ArtifactPointer, find_artifact() |
| `src/monet/core/manifest_handle.py` | New -- ManifestHandle, get_manifest() |
| `src/monet/manifest.py` | New -- configure_manifest() |
| `src/monet/core/catalogue.py` | write() accepts key param |
| `src/monet/core/stubs.py` | _progress_publisher ContextVar, emit_progress, write_artifact key |
| `src/monet/orchestration/_state.py` | WorkBrief typed, PlanningState pointer, dead types removed |
| `src/monet/orchestration/planning_graph.py` | Pointer-only planner_node, planning_failed, routing |
| `src/monet/orchestration/execution_graph.py` | load_plan pointer + compat shim, pre-check removed |
| `src/monet/orchestration/_invoke.py` | Guard removed, get_manifest pool, _forward_progress |
| `src/monet/orchestration/_validate.py` | get_manifest() replaces default_manifest |
| `src/monet/_run.py` | Pointer-based handoff |
| `src/monet/agents/planner/__init__.py` | Validate, keyed write, cross-check, _build_roster |
| `src/monet/server/_bootstrap.py` | configure_manifest(), conditional catalogue |
| `src/monet/server/_routes.py` | POST progress route, ArtifactPointer key |
| `src/monet/core/worker_client.py` | RemoteQueue progress methods |
| `src/monet/__init__.py` | Export get_manifest, find_artifact |
| `tests/conftest.py` | Update queue imports, add configure_manifest |
| `tests/test_queue*.py` | Update imports (5 files) |
| `tests/test_public_api.py` | Update imports |
| `tests/test_server_routes.py` | Update imports |
| `tests/test_planning_routing.py` | New -- parametrized route_from_planner |
| `tests/test_progress_streaming.py` | New -- progress round-trip |
| `tests/test_find_artifact.py` | New -- find_artifact edge cases |
| `tests/test_manifest_handle.py` | New -- ManifestHandle lifecycle |

---

## Follow-On Tasks (Not This Plan)

1. **Requeue-with-backoff**: workers that lack capability for a
   claimed task requeue it with backoff delay. Requires per-backend
   visibility timeout, max-requeue count, dead-letter path
   surfacing CAPABILITY_UNAVAILABLE.

2. **Push pool dispatch**: dispatcher that POSTs tasks to the
   pool's configured URL instead of enqueuing.

3. **Checkpoint compatibility shim removal**: delete the
   `work_brief` fallback branch in `load_plan` after confirming
   no in-flight runs remain on the old schema.

---

## Verification

After each step, before proceeding:
1. `uv run pytest` -- all tests pass
2. `uv run mypy src/` -- zero errors
3. `uv run ruff check .` -- clean

After all steps complete:
4. `grep -r "get_catalogue" src/monet/orchestration/` -- only
   `execution_graph.py:load_plan`
5. `grep -r "default_manifest" src/monet/agents/` -- zero results
6. `grep -r "default_manifest" src/monet/orchestration/` -- zero
   results
7. `grep -r "result.artifacts\[0\]"` -- zero results
8. `PlanningState` has no field that is both settable to None and
   consumed without a None check
9. Progress round-trip test passes
10. `planning_failed` terminal node reachable in all planner failure
    paths and emits OTel span
