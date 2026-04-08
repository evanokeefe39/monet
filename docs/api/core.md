# Core SDK API Reference

All exports from `monet`.

## Decorator

### `agent`

Two equivalent call forms — both register at decoration time (import time).

```python
# Form 1 — bound partial
researcher = agent("researcher")

@researcher(command="fast")
async def researcher_fast(task: str) -> str: ...

@researcher(command="deep")
async def researcher_deep(task: str) -> str: ...

# Form 2 — verbose
@agent(agent_id="writer", command="deep")
async def writer_deep(task: str) -> str: ...
```

The decorator has two jobs only: registration and context injection. Before calling the function it sets `contextvars` so `emit_progress`, `emit_signal`, `write_artifact`, `get_run_context`, and `get_run_logger` resolve correctly anywhere in the call stack — including inside `AgentStream.run()`.

- All function parameters must be valid `AgentRunContext` field names. Invalid names raise `TypeError` at decoration time.
- The verbose form requires `agent_id`; an empty value raises `ValueError`.
- The wrapper is always async. Sync functions are called synchronously inside the async wrapper.
- Returns `AgentResult` on every code path (success, typed exception, unexpected exception).
- If a string return exceeds `DEFAULT_CONTENT_LIMIT` (4000 bytes) and a catalogue backend is configured, the full content is offloaded to the catalogue (the pointer lands in `artifacts`) and `output` becomes a 200-character inline summary.

## Types

### `AgentResult`

```python
@dataclass(frozen=True)
class AgentResult:
    success: bool
    output: str | dict[str, Any] | None = None
    artifacts: list[ArtifactPointer] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    trace_id: str = ""
    run_id: str = ""

    def has_signal(self, signal_type: SignalType) -> bool: ...
    def get_signal(self, signal_type: SignalType) -> Signal | None: ...
```

`output` is for inline results only — a string summary, a structured dict, or `None`. `artifacts` lists every catalogue pointer the agent wrote. The two fields are distinct: the orchestrator never falls back from one to the other.

### `AgentRunContext`

```python
class AgentRunContext(TypedDict):
    task: str
    context: list[dict[str, Any]]
    command: str
    trace_id: str
    run_id: str
    agent_id: str
    skills: list[str]
```

Runtime context. Set via `ContextVar` by the decorator. Fields are injected into the function by name matching against the function's parameters.

### `Signal`

```python
class Signal(TypedDict):
    type: str
    reason: str
    metadata: dict[str, Any] | None
```

A non-fatal event accumulated during execution. Multiple signals can be emitted per invocation. Fatal conditions raise typed exceptions instead.

### `SignalType` and routing groups

Defined in `monet.signals` and re-exported from `monet`.

```python
class SignalType(StrEnum):
    # Control flow — orchestrator routes on these directly
    NEEDS_HUMAN_REVIEW
    ESCALATION_REQUIRED
    APPROVAL_REQUIRED
    INSUFFICIENT_CONTEXT
    DEPENDENCY_FAILED
    RATE_LIMITED
    TOOL_UNAVAILABLE

    # Informational — feeds QA reflection verdict
    LOW_CONFIDENCE
    PARTIAL_RESULT
    CONFLICTING_SOURCES
    REVISION_SUGGESTED

    # Audit — recorded in state, no routing consequence
    EXTERNAL_ACTION_TAKEN
    CONTENT_OFFLOADED
    SENSITIVE_CONTENT

    SEMANTIC_ERROR
```

```python
BLOCKING       # NEEDS_HUMAN_REVIEW, ESCALATION_REQUIRED, APPROVAL_REQUIRED
RECOVERABLE    # INSUFFICIENT_CONTEXT, DEPENDENCY_FAILED, RATE_LIMITED, TOOL_UNAVAILABLE
INFORMATIONAL  # LOW_CONFIDENCE, PARTIAL_RESULT, CONFLICTING_SOURCES, REVISION_SUGGESTED
AUDIT          # EXTERNAL_ACTION_TAKEN, CONTENT_OFFLOADED, SENSITIVE_CONTENT
ROUTING        # BLOCKING | RECOVERABLE
```

`collect_wave` and `route_after_reflection` read from these frozensets — never from raw string matching.

### `ArtifactPointer`

```python
class ArtifactPointer(TypedDict):
    artifact_id: str
    url: str
```

Reference to an artifact in the catalogue.

## Ambient functions

The ambient trio resolves the decorator's contextvars and forwards to the underlying primitive. All three work anywhere in the call stack beneath `@agent`, including inside `AgentStream.run()`.

### `emit_progress`

```python
def emit_progress(data: dict[str, Any]) -> None
```

Writes a progress event into the LangGraph stream writer. No-op outside a LangGraph execution context.

### `emit_signal`

```python
def emit_signal(signal: Signal) -> None
```

Appends a signal to the decorator's signal collector. No-op outside the `@agent` decorator context.

### `write_artifact`

```python
async def write_artifact(
    content: bytes,
    content_type: str,
    summary: str,
    confidence: float = 0.0,
    completeness: str = "complete",
    sensitivity_label: str = "internal",
) -> ArtifactPointer
```

Convenience alias for `await get_catalogue().write(...)`. The pointer is appended to `AgentResult.artifacts` automatically. Raises `NotImplementedError` if no catalogue backend is configured — call `monet.catalogue.configure_catalogue(...)` at startup.

### `get_catalogue`

```python
def get_catalogue() -> CatalogueHandle
```

Returns the context-aware catalogue handle. `await handle.write(...)` and `await handle.read(...)` are the canonical operations; `write_artifact` is the convenience alias.

### `get_run_context`

```python
def get_run_context() -> AgentRunContext
```

Returns the active `AgentRunContext`. Outside a decorated function, returns a default with empty fields.

### `get_run_logger`

```python
def get_run_logger() -> logging.LoggerAdapter
```

Structured logger pre-populated with `trace_id`, `run_id`, `agent_id`, and `command` from the current context.

## Streams

### `AgentStream`

Typed async event bus for external agents. See [`docs/guides/agents.md`](../guides/agents.md) for the full integration guide.

```python
from monet import AgentStream

@researcher(command="deep")
async def researcher_deep(task: str) -> None:
    await (
        AgentStream
        .cli(cmd=["./researcher", "--task", task, "--mode", "deep"])
        .on("artifact", write_artifact)
        .on("error", log_handler(logger, level="error"))
        .run()
    )
```

Named constructors:

| Constructor | Transport |
|---|---|
| `AgentStream.cli(cmd=[...])` | subprocess stdout, newline-delimited JSON |
| `AgentStream.sse(url=...)` | HTTP Server-Sent Events |
| `AgentStream.http(url=..., interval=...)` | HTTP polling |
| `AgentStream.grpc(...)` | reserved — subclass and override `_iter_events()` |

`.on(event_type, handler)` registers a handler at the point of use. Handlers are sync or async callables taking the event dict. Multiple handlers per event type are called in registration order. `.run()` returns the last `result.output` string, or `None`.

If no handler is registered for an event type, default routing applies:

| Event | Default |
|---|---|
| `progress` | `emit_progress(event)` |
| `signal` | `emit_signal(Signal(...))` |
| `artifact` | `await get_catalogue().write(...)` |
| `error` | `raise SemanticError(...)` |
| `result` | captured as `.run()` return value |
| unknown | log warning, continue |

Unknown `signal_type` values from the binary raise `ValueError` before any handler fires — version mismatches are loud failures.

### Handler factories

```python
from monet import webhook_handler, log_handler

webhook_handler(url: str)                    # async handler — POSTs event JSON
log_handler(logger: Logger, level: str = "info")  # sync handler — logs events
```

## Exceptions

### `NeedsHumanReview`

```python
class NeedsHumanReview(Exception):
    def __init__(self, reason: str = "") -> None
```

Agent requests human review. The decorator translates this to a `Signal(type=NEEDS_HUMAN_REVIEW, ...)`. Partial artifacts already written are preserved in `AgentResult.artifacts`.

### `EscalationRequired`

```python
class EscalationRequired(Exception):
    def __init__(self, reason: str = "") -> None
```

Agent has hit a capability or permissions boundary. Translated to `Signal(type=ESCALATION_REQUIRED, ...)`.

### `SemanticError`

```python
class SemanticError(Exception):
    def __init__(self, type: str = "unknown", message: str = "") -> None
```

Soft failure with a structured `type` and `message`. Translated to `Signal(type=SEMANTIC_ERROR, metadata={"error_type": type})`. Unexpected exceptions are wrapped as `SemanticError(type="unexpected_error")`.
