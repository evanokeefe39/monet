# Building Custom Graphs

monet ships with built-in planning, execution, and chat graphs. For workflows that don't fit that topology, build your own LangGraph StateGraph and register it as an entrypoint. The only SDK primitive you need is `invoke_agent`.

## Minimal custom graph

A custom graph is a standard LangGraph `StateGraph`. Nodes call `invoke_agent` to dispatch work to agents via the task queue. The graph owns topology; agents own execution.

```python
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from monet.orchestration import invoke_agent


class PipelineState(TypedDict, total=False):
    task: str
    research: str
    draft: str


async def research_node(state: PipelineState) -> dict[str, Any]:
    result = await invoke_agent(
        "researcher", command="deep", task=state.get("task", "")
    )
    return {"research": result.output or ""}


async def draft_node(state: PipelineState) -> dict[str, Any]:
    result = await invoke_agent(
        "writer",
        command="fast",
        task=f"{state.get('task', '')}\n\nFindings:\n{state.get('research', '')}",
    )
    return {"draft": result.output or ""}


def build_pipeline() -> StateGraph:
    graph: StateGraph[PipelineState] = StateGraph(PipelineState)
    graph.add_node("research", research_node)
    graph.add_node("draft", draft_node)
    graph.add_edge(START, "research")
    graph.add_edge("research", "draft")
    graph.add_edge("draft", END)
    return graph.compile()
```

Register as an entrypoint in `monet.toml`:

```toml
[entrypoints.my_pipeline]
graph = "my_pipeline"
```

Then invoke:

```bash
monet run --graph my_pipeline "write about quantum computing"
```

Or programmatically:

```python
async for event in client.run("my_pipeline", {"task": "write about quantum computing"}):
    print(event)
```

## Progress stream convention

Every `invoke_agent` call automatically emits lifecycle progress events:

| Status | When | Fields |
|---|---|---|
| `agent:started` | Before dispatch | `agent`, `command`, `run_id` |
| `agent:completed` | After successful completion | `agent`, `command`, `run_id` |
| `agent:failed` | After failure | `agent`, `command`, `run_id`, `reasons` |

Agents also emit their own freeform progress (e.g. `"searching with Exa"`, `"writing"`) between the lifecycle bookends. The colon prefix distinguishes lifecycle events from agent-authored ones.

Client-side, these arrive as `AgentProgress` events:

```python
from monet.client._events import AgentProgress

async for event in client.run("my_pipeline", {"task": "..."}):
    if isinstance(event, AgentProgress):
        print(f"{event.agent_id}:{event.command} — {event.status}")
```

A typical stream looks like:

```
researcher:deep — agent:started
researcher:deep — searching with Exa
researcher:deep — synthesising findings
researcher:deep — agent:completed
writer:fast — agent:started
writer:fast — writing
writer:fast — agent:completed
```

Custom graph nodes can emit their own progress for non-agent work:

```python
from monet import emit_progress

async def merge_node(state: PipelineState) -> dict[str, Any]:
    emit_progress({"status": "merging results", "agent": "pipeline"})
    # ... merge logic ...
    return {"merged": combined}
```

## Handling agent results

`invoke_agent` returns an `AgentResult`. Check `success`, inspect `signals`, and read `output`:

```python
async def guarded_node(state: PipelineState) -> dict[str, Any]:
    result = await invoke_agent("researcher", command="deep", task=state["task"])

    if not result.success:
        # Inspect signals for failure reason
        for signal in result.signals:
            if signal.type == "capability_unavailable":
                return {"error": "researcher not available"}
        return {"error": "research failed"}

    return {"research": result.output or ""}
```

Signals are informational — the graph decides what they mean. See [Signal types](../api/core.md#signaltype-and-routing-groups) for the full taxonomy.

## Fan-out (parallel agents)

Use LangGraph edges to run agents in parallel:

```python
from langgraph.graph import END, START, StateGraph


class FanOutState(TypedDict, total=False):
    task: str
    fast_result: str
    heavy_result: str


async def run_fast(state: FanOutState) -> dict[str, Any]:
    result = await invoke_agent("fast_agent", "fast", task=state.get("task", ""))
    return {"fast_result": result.output or ""}


async def run_heavy(state: FanOutState) -> dict[str, Any]:
    result = await invoke_agent("heavy_agent", "fast", task=state.get("task", ""))
    return {"heavy_result": result.output or ""}


def build_fanout() -> StateGraph:
    g: StateGraph[FanOutState] = StateGraph(FanOutState)
    g.add_node("fast", run_fast)
    g.add_node("heavy", run_heavy)
    g.add_edge(START, "fast")
    g.add_edge(START, "heavy")
    g.add_edge("fast", END)
    g.add_edge("heavy", END)
    return g.compile()
```

Each parallel agent gets its own lifecycle events in the stream. Clients can group by `agent_id` + `command` + `run_id`.

See [`examples/split-fleet/`](https://github.com/evanokeefe39/monet/tree/master/examples/split-fleet) for a complete parallel-pool example.

## Graph hook points

Custom graphs can declare hook points for extension without modifying the graph itself:

```python
from monet import GraphHookRegistry


def build_review_graph(hooks: GraphHookRegistry | None = None) -> StateGraph:
    async def review_with_hooks(state: ReviewState) -> dict[str, Any]:
        if hooks:
            state = await hooks.run("before_review", state)
        update = await review_node(state)
        if hooks:
            update = await hooks.run("after_review", update)
        return update

    graph = StateGraph(ReviewState)
    graph.add_node("draft", draft_node)
    graph.add_node("review", review_with_hooks if hooks else review_node)
    graph.set_entry_point("draft")
    graph.add_edge("draft", "review")
    return graph
```

See [`examples/custom-graph/`](https://github.com/evanokeefe39/monet/tree/master/examples/custom-graph) for hooks with before_agent/after_agent worker hooks and graph-level hook points.

## State design

Follow these conventions for custom state schemas:

- Use `TypedDict` with `total=False` so nodes return only the keys they change.
- Include `task`, `trace_id`, and `run_id` for tracing continuity.
- Keep state pointer-only: store content in the artifact store, pass `ArtifactPointer` references in state. Agents that need full content call `resolve_context()`.
- For append-only fields (results, reflections), use LangGraph's `Annotated` with a list reducer.

```python
from typing import Annotated, Any, TypedDict

def _append(existing: list, new: list) -> list:
    return existing + new

class MyState(TypedDict, total=False):
    task: str
    trace_id: str
    run_id: str
    results: Annotated[list[dict[str, Any]], _append]
```

## Registration and serving

Export your graph builder from `server_graphs.py` (or wherever your Aegra config points). Wrap as a 0-arg function for Aegra compatibility:

```python
# server_graphs.py
from myco.graphs.pipeline import build_custom_pipeline

# Aegra's factory classifier needs 0-arg functions
custom_pipeline = build_custom_pipeline
```

Declare entrypoints in `monet.toml`:

```toml
[entrypoints.custom_pipeline]
graph = "custom_pipeline"
```

The server discovers and serves the graph. `monet run --graph custom_pipeline` and `MonetClient.run("custom_pipeline", ...)` both work.

## Replacing the chat graph

monet ships a default chat graph (`orchestration/chat/`) that handles slash-command parsing, triage, direct LLM responses, specialist agent dispatch, and planning handoff. For self-hosted deployments you can replace it entirely with your own implementation.

### Minimal contract

The client and TUI are protocol-based — they never import types from `orchestration/chat`. A replacement graph must satisfy four points:

**1. Input shape**

```python
{"messages": [{"role": str, "content": str}]}
```

The `messages` field is the only required input key.

**2. State patches — messages field**

Emit state updates containing a `messages` list. The client reads the `messages` key from update events and yields chunks where `role == "assistant"`. Use an append-only reducer so prior history is not lost:

```python
from typing import Annotated, Any, TypedDict

def _append(existing: list, new: list) -> list:
    return existing + new

class MyChatState(TypedDict, total=False):
    messages: Annotated[list[dict[str, Any]], _append]
```

**3. Progress events (optional)**

Custom events with `agent`, `status`, and `run_id` keys become `AgentProgress` objects on the client and render as progress lines in the TUI:

```python
from monet import emit_progress

emit_progress({"agent": "mybot", "status": "thinking", "run_id": run_id})
```

Any events without these keys are silently ignored by the client.

**4. HITL interrupts (optional)**

Call LangGraph's `interrupt()` with any dict payload. The TUI renders the interrupt as raw text by default. If the payload has `prompt` and `fields` keys it renders as a structured form:

```python
from langgraph.types import interrupt

# Plain text prompt
interrupt({"prompt": "Confirm deletion?"})

# Structured form — TUI renders labeled fields
interrupt({
    "prompt": "Configure connection",
    "fields": [{"name": "host", "label": "Host", "type": "text"}],
})
```

Resume by calling `client.resume(run_id, tag, payload)`.

### Registration

```toml
# monet.toml
[chat]
graph = "myco.graphs.chat:build_chat_graph"
```

Or via env var: `MONET_CHAT_GRAPH=myco.graphs.chat:build_chat_graph`.

`monet chat` and `MonetClient.chat(...)` both pick up the replacement automatically. No other code changes required.

### What you do not need to replicate

- `ChatState`, `ChatTriageResult`, `build_chat_graph` — these are monet's internal implementation. Do not import or extend them.
- Slash-command parsing — your graph can define its own input conventions.
- `ChatConfig` — your graph loads whatever config it needs independently.

See [`examples/custom-stack/myco/graphs/chat.py`](https://github.com/evanokeefe39/monet/tree/master/examples/custom-stack/myco/graphs/chat.py) for a complete replacement that shares zero internals with the default implementation.

## Examples

| Example | What it demonstrates |
|---|---|
| [`custom-graph/`](https://github.com/evanokeefe39/monet/tree/master/examples/custom-graph) | Custom graph alongside monet's defaults, graph hook points, worker hooks (before_agent, after_agent) |
| [`custom-stack/`](https://github.com/evanokeefe39/monet/tree/master/examples/custom-stack) | Fully user-owned stack — bespoke agents, custom graphs, zero reuse of reference agents or default pipeline |
| [`split-fleet/`](https://github.com/evanokeefe39/monet/tree/master/examples/split-fleet) | Fan-out graph invoking agents on different worker pools |
