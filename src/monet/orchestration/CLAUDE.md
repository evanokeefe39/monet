# monet.orchestration — LangGraph Graphs

## Responsibility

Two-graph pipeline (planning + execution) plus chat graph. Owns graph construction, state management, agent dispatch, signal routing, HITL interrupt nodes.

## Directory structure

```
orchestration/
  _invoke.py          invoke_agent() — enqueues task, awaits result, routes signals
  _state.py           Core only: _append_reducer, AgentInvocationResult
  _signal_router.py   Maps signal types to graph transitions
  _retry_budget.py    Per-run retry budget tracking
  _result_parser.py   Parse AgentResult from queue into typed graph events
  prebuilt/           monet's shipped graph implementations (see below)
```

## Core modules (`orchestration/`)

These are stable utilities any custom graph may import from `monet.orchestration`:

| Module | Owns |
|--------|------|
| `_invoke.py` | `invoke_agent()` |
| `_state.py` | `_append_reducer`, `AgentInvocationResult` |
| `_signal_router.py` | Signal routing |
| `_retry_budget.py` | Retry budget |
| `_result_parser.py` | Parse AgentResult from queue into typed graph events |

`AgentInvocationResult` is the universal shape returned by `invoke_agent()`. The `id` field is caller-assigned; the prebuilt execution graph sets it to `RoutingNode.id`.

## Prebuilt subpackage (`orchestration/prebuilt/`)

monet's default planning/execution/chat implementations. Custom graphs import from the core modules above, not from `prebuilt/`.

| Module | Owns |
|--------|------|
| `planning_graph.py` | Planning subgraph — LLM planner, HITL revision loop (MAX_REVISIONS=3), emits `work_brief_pointer` + `RoutingSkeleton` |
| `execution_graph.py` | Execution subgraph — DAG traversal via `completed_node_ids`, fans out via Send |
| `default_graph.py` | Compound graph: planning → execution |
| `chat_graph.py` | Thin Aegra-compatible entry point — **absolute imports only** (Aegra re-executes under synthetic namespace); delegates to `chat/` |
| `chat/` | monet's default chat implementation — private subpackage, see below |
| `_state.py` | All prebuilt TypedDicts: RoutingNode, RoutingSkeleton, WorkBrief, WorkBriefNode, RunState, PlanningState, SignalsSummary, ExecutionState, WaveItem |
| `_planner_outcome.py` | Parse planner structured output into RoutingSkeleton |
| `_forms.py` | HITL form-schema builders for plan-approval and execution interrupts |

## Chat subpackage (`prebuilt/chat/`)

monet's default chat graph implementation. All modules are private (`_*.py`). Neither the client nor the TUI imports from this package — the contract is protocol-based, not type-based.

| Module | Owns |
|--------|------|
| `_state.py` | `ChatState` TypedDict — messages (append reducer), route, command_meta |
| `_build.py` | `build_chat_graph()` — composes nodes and conditional edges into compiled graph |
| `_parse.py` | `parse_command_node` — pure-string slash-command parser, no LLM |
| `_triage.py` | `triage_node` — LLM classifier routing free-form text to chat/plan/specialist |
| `_respond.py` | `respond_node` — direct LLM reply for conversational turns |
| `_specialist.py` | `specialist_node` — dispatches `/<agent>:<cmd>` to `invoke_agent` |
| `_format.py` | Render `AgentResult` and execution summaries as chat messages |
| `_lc.py` | LangChain model binding (`init_chat_model`); single touch-point for LLM provider |

### Chat graph contract (for replacement graphs)

The client and TUI invoke the chat graph by string ID only. A replacement graph must:

1. Accept `{"messages": [{"role": str, "content": str}]}` as input.
2. Emit state patches with a `messages` field (same shape, append-only reducer).
3. Optionally emit custom progress events as dicts with `agent`, `status`, `run_id` keys.
4. Optionally call LangGraph `interrupt(payload_dict)` for HITL; payload with `prompt` + `fields` keys renders as a form in the TUI.

`ChatState`, `ChatTriageResult`, and `build_chat_graph` are implementation details — do not extend them in replacement graphs. Configure replacement via `[chat] graph = "mymod:factory"` in `monet.toml`. Full replacement guide: `docs/guides/custom-graphs.md#replacing-the-chat-graph`.

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
