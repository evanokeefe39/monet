# Orchestration API Reference

All exports from `monet.orchestration`.

## State types

### `GraphState`

```python
class GraphState(TypedDict, total=False):
    task: str
    trace_id: str
    run_id: str
    results: Annotated[list[dict[str, Any]], _append_reducer]
    needs_review: bool
```

Top-level LangGraph state. The `results` field uses an append reducer -- new entries are concatenated with existing entries rather than replacing them.

### `AgentStateEntry`

```python
class AgentStateEntry(TypedDict, total=False):
    agent_id: str
    command: str
    effort: str
    output: str
    artifact_url: str
    summary: str
    confidence: float
    completeness: str
    success: bool
    needs_human_review: bool
    escalation_requested: bool
    semantic_error: dict[str, str] | None
    trace_id: str
    run_id: str
```

A single agent result entry in graph state. All fields are optional (`total=False`).

## Functions

### `create_node`

```python
def create_node(
    agent_id: str,
    command: str = "fast",
    content_limit: int = 4000,
    *,
    interrupt_on_review: bool = True,
) -> Callable[[GraphState], Coroutine[Any, Any, dict[str, Any]]]
```

Creates a LangGraph node function for an agent. The returned async function handles OTel spans, agent invocation, result translation, content limit enforcement, and HITL interrupts.

The node function's `__name__` and `__qualname__` are set to `"{agent_id}_{command}"`.

### `invoke_agent`

```python
async def invoke_agent(
    agent_id: str,
    command: str,
    ctx: AgentRunContext,
) -> AgentResult
```

Transport-agnostic agent invocation. Routes based on environment configuration:

- **Local** (default): looks up the handler in the default registry, calls directly
- **HTTP**: POSTs to the endpoint from `MONET_AGENT_{AGENT_ID}_URL`

Environment variables:

- `MONET_AGENT_TRANSPORT` -- `"http"` for HTTP mode, anything else for local
- `MONET_AGENT_{AGENT_ID}_URL` -- HTTP endpoint for a specific agent

### `build_retry_policy`

```python
def build_retry_policy(descriptor: CommandDescriptor) -> RetryPolicy
```

Converts a `CommandDescriptor`'s `RetryConfig` into a LangGraph `RetryPolicy`. Sets `max_attempts = max_retries + 1`.

### `enforce_content_limit`

```python
def enforce_content_limit(
    entry: dict[str, Any],
    limit: int = 4000,
) -> dict[str, Any]
```

Checks output length against limit. If over limit and a catalogue client is available, writes full content to the catalogue and replaces the output with a truncated summary plus `artifact_url`. If no catalogue, truncates directly. Returns the (possibly modified) entry dict.
