# Refactor: orchestration/prebuilt + hooks/prebuilt separation

## Goal
Separate `orchestration/` and `hooks/` into core vs `prebuilt/`. Core = protocols, utilities any graph/hook needs. Prebuilt = monet's shipped implementations. Follows same pattern as `queue/backends/`, `progress/backends/`.

Additionally: introduce `AgentInvocationResult` as core type, retire `WaveResult`.

## Classification

### orchestration/

**Core** (stays at package root):
- `_state.py` — retains only `_append_reducer` and `AgentInvocationResult`
- `_invoke.py` — queue-based agent invocation, configure_queue, get_queue
- `_forms.py` — HITL form builders
- `_signal_router.py` — declarative signal-to-action mapping
- `_retry_budget.py` — shared retry counter
- `_result_parser.py` — structured output parsing

**Prebuilt** (moves to `prebuilt/`):
- `_state.py` (new) — all prebuilt state schemas: `RoutingNode`, `RoutingSkeleton`, `WorkBrief`, `WorkBriefNode`, `RunState`, `PlanningState`, `ExecutionState`, `SignalsSummary`, `WaveItem`
- `_planner_outcome.py` — planner result classification
- `default_graph.py` — compound planning+execution graph
- `planning_graph.py` — planning subgraph
- `execution_graph.py` — execution subgraph
- `chat_graph.py` — Aegra entry point (absolute imports)
- `chat/` — entire subpackage (7 files)

### hooks/

- `core/hooks.py` — core (no move needed, already in place)
- `hooks/plan_context.py` → `hooks/prebuilt/plan_context.py`

## AgentInvocationResult

New core TypedDict in `orchestration/_state.py`. Universal shape for a completed `invoke_agent()` call. Replaces `WaveResult` — prebuilt uses `AgentInvocationResult` directly with `id` set to `RoutingNode.id`.

```python
class AgentInvocationResult(TypedDict):
    """Universal shape for a completed agent invocation.

    Any graph calling invoke_agent() writes results in this shape.
    ``id`` is caller-assigned — prebuilt sets it to RoutingNode.id,
    custom graphs use whatever invocation identity they need.
    """
    id: str
    agent_id: str
    command: str
    output: str | dict[str, Any] | None
    artifacts: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    success: bool
```

`WaveResult` is deleted. All references in prebuilt (`execution_graph.py`, `chat/_format.py`, tests) update to `AgentInvocationResult`.

## Checklist

### Phase 0: Introduce AgentInvocationResult, delete WaveResult
- [ ] Add `AgentInvocationResult` to `orchestration/_state.py`
- [ ] Delete `WaveResult` from `orchestration/_state.py`
- [ ] Update `orchestration/__init__.py` exports: `WaveResult` → `AgentInvocationResult`
- [ ] Update all consumers of `WaveResult` (grep for it — execution_graph, chat/_format, tests)
- [ ] Verify: `uv run mypy src/ && uv run pytest -q --ignore=tests/e2e --ignore=tests/compat --ignore=tests/chat 2>&1 | tail -60`

### Phase 1: Split _state.py
- [ ] Create `orchestration/prebuilt/_state.py` with: `RoutingNode`, `RoutingSkeleton`, `WorkBrief`, `WorkBriefNode`, `RunState`, `PlanningState`, `ExecutionState`, `SignalsSummary`, `WaveItem`
- [ ] Core `orchestration/_state.py` retains: `_append_reducer`, `AgentInvocationResult`
- [ ] Prebuilt `_state.py` imports `_append_reducer` from core: `from monet.orchestration._state import _append_reducer`
- [ ] Update all imports of moved schemas (grep each symbol name)

### Phase 2: Create prebuilt/ and move graph files
- [ ] Create `orchestration/prebuilt/__init__.py` — re-exports `build_chat_graph`, `build_default_graph`, `build_execution_subgraph`, `build_planning_subgraph`, `ChatState`, and prebuilt state schemas
- [ ] Move `_planner_outcome.py` → `prebuilt/_planner_outcome.py`
- [ ] Move `default_graph.py` → `prebuilt/default_graph.py`
- [ ] Move `planning_graph.py` → `prebuilt/planning_graph.py`
- [ ] Move `execution_graph.py` → `prebuilt/execution_graph.py`
- [ ] Move `chat_graph.py` → `prebuilt/chat_graph.py`
- [ ] Move `chat/` → `prebuilt/chat/`

### Phase 3: Fix internal imports within prebuilt/
Prebuilt→core: absolute (`from monet.orchestration._invoke import ...`)
Prebuilt→prebuilt: relative (`from .planning_graph import ...`)
Exception: `chat_graph.py` uses absolute (Aegra synthetic namespace)

- [ ] `prebuilt/default_graph.py`: relative imports to sibling graph modules
- [ ] `prebuilt/planning_graph.py`: relative to `._planner_outcome`, `._state`; absolute to core `monet.orchestration._invoke` etc.
- [ ] `prebuilt/execution_graph.py`: same pattern
- [ ] `prebuilt/chat/_build.py`: `from monet.orchestration.execution_graph` → `from ..execution_graph`
- [ ] `prebuilt/chat/_format.py`: `from monet.orchestration._planner_outcome` → `from .._planner_outcome`
- [ ] `prebuilt/chat/_state.py`: `from monet.orchestration._state` stays absolute (core)
- [ ] `prebuilt/chat/_specialist.py`: `from monet.orchestration._invoke` stays absolute (core)
- [ ] `prebuilt/chat_graph.py`: `from monet.orchestration.chat` → `from monet.orchestration.prebuilt.chat`

### Phase 4: Fix external imports pointing at moved modules
- [ ] `server/server_bootstrap.py`: update orchestration imports
- [ ] `config/_schema.py`: `_DEFAULT_CHAT_GRAPH` path → `monet.orchestration.prebuilt.chat_graph:build_chat_graph`
- [ ] `server/_smoke.py`: `from monet.orchestration.chat._lc` → `from monet.orchestration.prebuilt.chat._lc`
- [ ] `hooks/plan_context.py`: `from monet.orchestration._state import WorkBrief` → `from monet.orchestration.prebuilt._state import WorkBrief`
- [ ] `agents/planner/__init__.py`: same WorkBrief import update
- [ ] Test files: grep and update any direct imports of moved modules

### Phase 5: Update orchestration/__init__.py
- [ ] Rewrite imports: core from local `._*`, prebuilt chain from `.prebuilt`
- [ ] `__all__` updated: `WaveResult` → `AgentInvocationResult`, rest unchanged
- [ ] Update module docstring to document core vs prebuilt split

### Phase 6: hooks/prebuilt separation
- [ ] Create `hooks/prebuilt/__init__.py` — imports `plan_context` for side-effect registration
- [ ] Move `hooks/plan_context.py` → `hooks/prebuilt/plan_context.py`
- [ ] Update `hooks/__init__.py`: `from . import plan_context` → `from .prebuilt import plan_context`
- [ ] `hooks/prebuilt/plan_context.py`: WorkBrief import already updated in Phase 4

### Phase 7: Update CLAUDE.md files
- [ ] `orchestration/CLAUDE.md` — reflect core vs prebuilt split, new directory structure
- [ ] `hooks/CLAUDE.md` — reflect prebuilt/ subdirectory

### Phase 8: Verify
- [ ] `uv run ruff check .`
- [ ] `uv run ruff format .`
- [ ] `uv run mypy src/`
- [ ] `uv run pytest tests/orchestration/ -q 2>&1 | tail -60`
- [ ] `uv run pytest -q --ignore=tests/e2e --ignore=tests/compat --ignore=tests/chat 2>&1 | tail -60`

## Import strategy

Prebuilt→core: **absolute** (`from monet.orchestration._invoke import ...`). Core is stable, absolute is explicit.

Prebuilt→prebuilt: **relative** (`from .planning_graph import ...`). Siblings in same subpackage.

Exception: `chat_graph.py` must use absolute imports (Aegra re-executes under synthetic namespace).

## Risk items

1. **Aegra path resolution** — `server_bootstrap.py` is what Aegra sees, not `chat_graph.py` directly. Only `server_bootstrap.py` import paths change. Low risk.

2. **`_DEFAULT_CHAT_GRAPH` in config** — default string literal changes. Users who hardcoded `monet.orchestration.chat_graph:build_chat_graph` in `monet.toml` would break — but this was never documented as stable API. The `[chat] graph` config key is the contract.

3. **WaveResult deletion** — any user code referencing `WaveResult` by name breaks. Acceptable: it was a TypedDict in a private module, and `AgentInvocationResult` is a direct superset (adds `id`, otherwise same fields minus `node_id`).

4. **Test imports** — grep in Phase 4 catches these.

## Target directory structure

```
orchestration/
├── __init__.py              # re-exports core + prebuilt public API
├── _state.py                # core: _append_reducer, AgentInvocationResult
├── _invoke.py               # core
├── _forms.py                # core
├── _signal_router.py        # core
├── _retry_budget.py         # core
├── _result_parser.py        # core
├── prebuilt/
│   ├── __init__.py          # re-exports build_* functions + prebuilt state
│   ├── _state.py            # RoutingNode, RoutingSkeleton, WorkBrief, RunState, etc.
│   ├── _planner_outcome.py
│   ├── default_graph.py
│   ├── planning_graph.py
│   ├── execution_graph.py
│   ├── chat_graph.py
│   └── chat/
│       ├── __init__.py
│       ├── _state.py
│       ├── _build.py
│       ├── _parse.py
│       ├── _triage.py
│       ├── _respond.py
│       ├── _specialist.py
│       ├── _format.py
│       └── _lc.py

hooks/
├── __init__.py              # side-effect import chain
├── prebuilt/
│   ├── __init__.py
│   └── plan_context.py
```
