# monet.orchestration — LangGraph Graphs

## Responsibility

Two-graph pipeline (planning + execution) plus chat graph. Owns graph construction, state management, agent dispatch, signal routing, HITL interrupt nodes.

## Key modules

| Module | Owns |
|--------|------|
| `planning_graph.py` | Planning subgraph — LLM planner, HITL revision loop (MAX_REVISIONS=3), emits `work_brief_pointer` + `RoutingSkeleton` |
| `execution_graph.py` | Execution subgraph — DAG traversal via `completed_node_ids`, dispatches `RoutingNode` to queue |
| `default_graph.py` | Compound graph: planning → execution |
| `chat_graph.py` | Chat graph — triage node, respond node, HITL interrupt |
| `_invoke.py` | `invoke_agent()` — builds `TaskRecord`, enqueues, awaits result, routes signals |
| `_state.py` | `OrchestrationState` TypedDict — pointer-only, no content |
| `_signal_router.py` | Maps signal types to graph transitions |
| `_retry_budget.py` | Per-run retry budget tracking |
| `_result_parser.py` | Parse `AgentResult` from queue into typed graph events |
| `_planner_outcome.py` | Parse planner structured output into `RoutingSkeleton` |
| `_forms.py` | HITL form schema (`Form`, `Field`) for interrupt payloads |

## Pointer-only invariant

Planner writes full `work_brief` to artifact store, emits `work_brief_pointer` (key) + inline `RoutingSkeleton ({goal, nodes})`. State never holds content — only pointers. `inject_plan_context` hook on worker side resolves pointer at invocation time.

## DAG execution

`RoutingNode`: `{id, agent_id, command, depends_on}`. Execution traverses by `completed_node_ids`. Static DAG — fan-out N uses list-input agent commands, not plan-time node fan-out.

## Graph hooks

`GraphHookRegistry` — server-process hooks declared at graph construction points. Distinct from worker-side `@on_hook`.

## What orchestration does NOT own

- Agent logic
- Queue transport (calls `monet.queue` via `_invoke.py`)
- Config loading
- Triage at pipeline entry — no simple_triage_node; explicit invocations call planning directly

## Invariants

- No `_assert_registered` in graph builders — capabilities may not exist at construction time
- `monet run` and chat's `/plan` both invoke planning directly (no pipeline short-circuit)
- Revise-with-feedback lives inside planning subgraph's HITL loop only
