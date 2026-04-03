# Orchestration

The orchestration layer integrates monet agents with LangGraph. It provides node wrappers, state schemas, and utilities that handle the bridge between agent invocations and graph execution.

## Graph state

LangGraph state entries are always lean. Full artifact content never lives in graph state -- only summaries, pointers, confidence scores, and signals.

### `GraphState`

The top-level state for a monet LangGraph graph:

| Field | Type | Description |
|---|---|---|
| `task` | `str` | The user's task |
| `trace_id` | `str` | OTel trace ID |
| `run_id` | `str` | LangGraph run ID |
| `results` | `list[dict]` | Agent result entries (append-only via reducer) |
| `needs_review` | `bool` | Whether any agent flagged human review |

### `AgentStateEntry`

Each entry in `results` follows this schema:

| Field | Type | Description |
|---|---|---|
| `agent_id` | `str` | Which agent produced this |
| `command` | `str` | Which command was invoked |
| `effort` | `str` | Effort level passed at invocation |
| `output` | `str` | Inline result or artifact URL |
| `artifact_url` | `str` | Catalogue URL if offloaded |
| `summary` | `str` | Bounded summary |
| `confidence` | `float` | 0.0--1.0 |
| `completeness` | `str` | `complete`, `partial`, or `resource-bounded` |
| `success` | `bool` | Whether the agent completed without error |
| `needs_human_review` | `bool` | Human review signal |
| `escalation_requested` | `bool` | Escalation signal |
| `semantic_error` | `dict \| None` | Error info (`type` + `message`) |
| `trace_id` | `str` | OTel trace ID |
| `run_id` | `str` | LangGraph run ID |

## Creating nodes

`create_node()` is the factory for LangGraph node functions:

```python
from monet.orchestration import create_node

researcher_node = create_node(agent_id="researcher", command="deep")
writer_node = create_node(agent_id="writer", command="fast", content_limit=2000)
```

Parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `agent_id` | `str` | required | The agent's registered ID |
| `command` | `str` | `"fast"` | Which command to invoke |
| `content_limit` | `int` | `4000` | Max output chars before offload |
| `interrupt_on_review` | `bool` | `True` | Call `interrupt()` on `needs_human_review` |

The returned async function:

1. Starts an OTel span with agent ID, command, and run context
2. Constructs an `AgentRunContext` from the graph state
3. Calls `invoke_agent()` (local or HTTP based on configuration)
4. Translates `AgentResult` to a lean state entry
5. Enforces the content limit
6. Calls `langgraph.interrupt()` if `needs_human_review` is `True` and `interrupt_on_review` is enabled
7. Returns a state update dict with the result appended to `results`

## Building a graph

```python
from langgraph.graph import StateGraph

from monet.orchestration import GraphState, create_node

# Define nodes
researcher = create_node(agent_id="researcher", command="deep")
writer = create_node(agent_id="writer", command="fast")

# Build graph
graph = StateGraph(GraphState)
graph.add_node("research", researcher)
graph.add_node("write", writer)
graph.add_edge("research", "write")
graph.set_entry_point("research")
graph.set_finish_point("write")

app = graph.compile()

# Run
result = await app.ainvoke({
    "task": "Research and write about quantum computing",
    "trace_id": "trace-123",
    "run_id": "run-456",
})
```

## Agent invocation

`invoke_agent()` is transport-agnostic. It routes to local or HTTP invocation based on configuration:

- **Local**: looks up the handler in the agent registry and calls it directly as a Python function
- **HTTP**: POSTs to the agent's endpoint with the `AgentRunContext` serialised as JSON

Transport mode is controlled by environment variables:

- `MONET_AGENT_TRANSPORT` -- set to `"http"` for HTTP mode (default is local)
- `MONET_AGENT_{AGENT_ID}_URL` -- endpoint URL for a specific agent in HTTP mode

## Content limit enforcement

`enforce_content_limit()` is called by the node wrapper after each agent response. If the output exceeds the configured limit:

1. If a catalogue client is available: writes the full content to the catalogue, replaces the output with a truncated summary and the `artifact_url`
2. If no catalogue: truncates the output directly

This keeps graph state lean regardless of how much content agents produce.

## Retry policy

`build_retry_policy()` converts an agent's `CommandDescriptor.retry` configuration into a LangGraph `RetryPolicy`:

```python
from monet.descriptors import AgentDescriptor, CommandDescriptor, RetryConfig
from monet.orchestration import build_retry_policy

descriptor = AgentDescriptor(
    agent_id="researcher",
    commands={
        "deep": CommandDescriptor(
            retry=RetryConfig(max_retries=3, retryable_errors=["unexpected_error"])
        )
    },
)

policy = build_retry_policy(descriptor.commands["deep"])
```

`SemanticError` with `type="unexpected_error"` triggers retry if the descriptor declares it retryable.

## Human-in-the-loop

HITL is the orchestrator's concern, not the agent's. It is implemented in two ways:

**Structural checkpoints** -- nodes that always require human review use LangGraph's `interrupt_before` at graph compile time:

```python
graph.add_node("publish", publisher_node)
app = graph.compile(interrupt_before=["publish"])
```

**Policy-driven checkpoints** -- the node wrapper calls `langgraph.interrupt()` when `needs_human_review` is `True` in the agent's signals. This is enabled by default (`interrupt_on_review=True` on `create_node()`). The interrupt payload includes the agent ID, the review reason, and the full state entry.

Agents emit honest signals. The orchestrator decides what action each signal triggers.

## Planned features

!!! info "Coming soon"
    The following orchestration features are designed but not yet implemented:

- **Supervisor graph topology** -- three-graph system (triage, planning, execution) with wave-based parallel execution. See [Graph Topology](../architecture/graph-topology.md).
- **QA as structural safeguard** -- QA agent invoked by orchestrator policy, independent of producing agent signals
- **Post-wave reflection** -- QA checkpoint after each execution wave
- **Work brief structure** -- structured plan artifact with phases, dependency waves, and quality criteria
- **Postgres checkpointing** -- durable graph execution state (currently uses in-memory or SQLite checkpointers)
- **Durable execution patterns** -- Temporal integration for intra-invocation durability on long-running agents
