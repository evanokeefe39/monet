# Fix: LangGraph state scoping for multi-run chat threads

## Problem

On same-thread multi-run conversations, stale execution state (wave_results, completed_node_ids) and duplicate messages leak across runs. Root cause: transient execution data lives in parent ChatState with append reducers instead of being scoped to the execution subgraph.

## Goal

Align with LangGraph best practices:
- Thread-scoped = `messages` only (using `add_messages` reducer)
- Run-scoped transient data stays inside subgraphs
- Parent graph only receives distilled output (messages)

## Checklist

### Phase 1: Move execution_summary_node into execution subgraph

This is the key structural fix. execution_summary_node currently lives in the chat graph and reads wave_results from ChatState. Moving it inside the execution subgraph means wave_results never needs to leave ExecutionState.

- [ ] Move `execution_summary_node` from `chat/_format.py` into `execution_graph.py` as the final node before END
- [ ] Update execution subgraph topology: `collect_batch → (route_after_collect) → {dispatch | human_interrupt | END}` becomes `... → END → execution_summary` — actually: add edge from the END-bound route to a new "summarise" node, then that node → END
- [ ] `execution_summary_node` writes to `messages` field (add `messages: Annotated[list, _append_reducer]` to ExecutionState if not present)
- [ ] The summary message flows back to parent ChatState via name-matching on `messages`
- [ ] Remove `execution_summary` node from chat graph (`_build.py`)
- [ ] Remove edge `execution → execution_summary → END`, replace with `execution → END`
- [ ] Verify: graph topology test still passes

### Phase 2: Remove wave_results/wave_reflections from ChatState and RunState

Once execution_summary_node is inside the subgraph, parent graphs no longer need wave_results.

- [ ] Remove `wave_results` and `wave_reflections` from `ChatState` (chat/_state.py)
- [ ] Remove `wave_results` and `wave_reflections` from `RunState` (prebuilt/_state.py)
- [ ] Keep them in `ExecutionState` — they're correctly scoped there
- [ ] Remove `completed_node_ids` from `ChatState` (it's already only in ExecutionState + the parse reset)
- [ ] Remove the `completed_node_ids` reset from `parse_command_node` (no longer needed)
- [ ] Remove `wave_results`/`wave_reflections` resets from `parse_command_node`
- [ ] Verify: mypy passes, tests pass

### Phase 3: Switch messages to add_messages reducer

Replace `_append_reducer` on messages with LangGraph's `add_messages` which deduplicates by message ID and prevents the double-user-message bug.

- [ ] Import `add_messages` from `langgraph.graph.message`
- [ ] Replace `Annotated[list[dict[str, Any]], _append_reducer]` with `Annotated[list, add_messages]` for the `messages` field in `PlanningState`
- [ ] Ensure all message dicts emitted by nodes include an `id` field (or use LangChain message objects)
- [ ] If `add_messages` expects LangChain BaseMessage objects, evaluate whether to keep dict messages with a custom ID-aware reducer instead
- [ ] Alternative: write a `_dedup_messages_reducer` that deduplicates by content hash for dict-based messages (simpler, no BaseMessage dependency)
- [ ] Test: sending same message twice on same thread doesn't produce duplicate in state
- [ ] Remove the `update_state` + `input={}` double-write pattern in `send_message` if add_messages handles it, OR ensure message IDs prevent duplication

### Phase 4: Reset planning_context at planning entry

`planning_context` uses append reducer and grows across runs. Each new plan on the same thread shouldn't inherit old context entries from unrelated previous plans.

- [ ] In the planner node (or a new `initialise_planning` entry node), reset `planning_context` to `[]` at the start of each planning invocation
- [ ] OR: change `planning_context` from append reducer to plain list (overwritten each run)
- [ ] Evaluate: is there a case where carrying forward old context is desired? (e.g., "plan something similar to before") — if yes, keep append but scope to the current `task`
- [ ] Decision: likely reset is correct — the planner gets conversation history via `messages`, it doesn't need raw planning_context from prior unrelated plans

### Phase 5: Remove client-side workarounds

Once state scoping is correct, the band-aids can go.

- [ ] Remove the `seen_content` pre-seed from `_stream_chat_with_input` in `chat.py` (no stale messages in stream)
- [ ] Remove `plan_approved`, `work_brief_pointer`, `routing_skeleton` resets from `parse_command_node` (these are overwritten by the planning subgraph each run anyway; the issue was wave_results leaking)
- [ ] Evaluate: keep the parse resets as defense-in-depth? Costs nothing, prevents future bugs if someone adds a new conditional edge that reads old state. Decision: keep them, they're cheap.
- [ ] Remove the `_subgraph_parents` dedup logic in `stream_run` only if parent echoes no longer carry stale messages — test this empirically

### Phase 6: Verify end-to-end

- [ ] `uv run ruff check .`
- [ ] `uv run ruff format .`  
- [ ] `uv run mypy src/`
- [ ] `uv run pytest tests/chat/ tests/orchestration/ -q --ignore=tests/e2e --ignore=tests/compat 2>&1 | tail -60`
- [ ] Manual test: run two different plans on same chat thread, verify no duplicate "Execution finished"
- [ ] Manual test: verify wave_results don't carry old entries into new execution
- [ ] Manual test: verify conversation history displays correctly in TUI after multiple runs

## Architecture after changes

```
ChatState (parent):
  messages: Annotated[list, add_messages]   # thread-scoped, deduped
  route: str | None                         # run-scoped (overwritten by parse)
  command_meta: dict                        # run-scoped
  task: str                                 # run-scoped
  + PlanningState fields (plan_approved, routing_skeleton, etc.)

PlanningState (subgraph):
  messages: Annotated[list, add_messages]   # flows to parent
  task, work_brief_pointer, routing_skeleton, plan_approved, ...
  planning_context: list[dict]              # run-scoped (reset at entry)

ExecutionState (subgraph):
  messages: Annotated[list, add_messages]   # execution_summary writes here → flows to parent
  wave_results: Annotated[list, _append_reducer]   # stays here, never leaves
  wave_reflections: Annotated[list, _append_reducer]  # stays here
  completed_node_ids: list[str]             # stays here
  routing_skeleton, work_brief_pointer, ...
```

State flow:
- ChatState.messages ←(name-match)→ PlanningState.messages ←(name-match)→ ExecutionState.messages
- wave_results only exists in ExecutionState — scoped to one execution run
- execution_summary_node runs inside execution subgraph, writes to messages
- Parent chat graph receives the summary as a message, never sees raw wave_results

## Risk items

1. `add_messages` expects LangChain BaseMessage objects with `id` fields — if monet uses plain dicts, need either a custom reducer or conversion layer
2. Moving execution_summary_node changes the stream event namespace — client may need update to where it expects the summary event
3. `default_graph.py` (pipeline, non-chat) also has wave_results in RunState — same fix applies there
4. Tests that assert on wave_results in ChatState output will break — update to check messages instead
