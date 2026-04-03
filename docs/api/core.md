# Core SDK API Reference

All exports from `monet`.

## Decorator

### `agent`

```python
@agent(agent_id: str, command: str = "fast")
```

Wraps a callable as an agent handler. Registers it in the default registry under `(agent_id, command)`.

- All function parameters must be valid `AgentRunContext` field names. Invalid names raise `TypeError` at decoration time.
- `agent_id` is required. Calling `@agent` without it raises `TypeError`.
- The function's docstring is captured and stored with the registration.
- The wrapper is always async. Sync functions are called synchronously inside the async wrapper.
- Returns `AgentResult` on every code path (success, typed exception, unexpected exception).

## Types

### `AgentResult`

```python
@dataclass(frozen=True)
class AgentResult:
    success: bool
    output: str | ArtifactPointer
    confidence: float = 0.0
    artifacts: list[ArtifactPointer] = field(default_factory=list)
    signals: AgentSignals = field(default_factory=AgentSignals)
    trace_id: str = ""
    run_id: str = ""
```

Wrapped result from an agent invocation. Never constructed manually by agent authors -- the decorator builds it from the function's return value or raised exception.

### `AgentRunContext`

```python
@dataclass
class AgentRunContext:
    task: str = ""
    context: list[ContextEntry] = field(default_factory=list)
    command: str = "fast"
    effort: Effort | None = None
    trace_id: str = ""
    run_id: str = ""
    agent_id: str = ""
    skills: list[str] = field(default_factory=list)
```

Runtime context available inside a decorated agent function. Set via `ContextVar` by the decorator. Fields are injected into the function by name matching.

### `AgentSignals`

```python
@dataclass(frozen=True)
class AgentSignals:
    needs_human_review: bool = False
    review_reason: str | None = None
    escalation_requested: bool = False
    escalation_reason: str | None = None
    revision_notes: dict[str, Any] | None = None
    semantic_error: SemanticErrorInfo | None = None
```

Signals emitted by an agent, read by the orchestrator. Populated from typed exceptions by the decorator.

### `SemanticErrorInfo`

```python
@dataclass(frozen=True)
class SemanticErrorInfo:
    type: str
    message: str
```

Structured info for a semantic error signal.

### `ArtifactPointer`

```python
@dataclass(frozen=True)
class ArtifactPointer:
    artifact_id: str
    url: str
```

Reference to an artifact in the catalogue.

### `Effort`

```python
Effort = Literal["low", "medium", "high"]
```

### Context entry types

All context entry types are Pydantic `BaseModel` subclasses with common fields: `type` (literal discriminator), `summary`, `url`, `content`, `content_type`.

| Class | `type` value |
|---|---|
| `ArtifactEntry` | `"artifact"` |
| `WorkBriefEntry` | `"work_brief"` |
| `ConstraintEntry` | `"constraint"` |
| `InstructionEntry` | `"instruction"` |
| `SkillReferenceEntry` | `"skill_reference"` |

`ContextEntry` is the discriminated union type:

```python
ContextEntry = Annotated[
    ArtifactEntry | WorkBriefEntry | ConstraintEntry
    | InstructionEntry | SkillReferenceEntry,
    Field(discriminator="type"),
]
```

## Functions

### `get_run_context`

```python
def get_run_context() -> AgentRunContext
```

Returns the current `AgentRunContext` from the `ContextVar`. Inside a decorated function, returns the active context. Outside, returns a safe default with empty fields.

### `get_run_logger`

```python
def get_run_logger() -> logging.LoggerAdapter
```

Returns a structured logger pre-populated with `trace_id`, `run_id`, `agent_id`, and `command` from the current context. Returns a no-op logger outside the decorator.

### `write_artifact`

```python
def write_artifact(
    content: bytes,
    content_type: str,
    summary: str = "",
    confidence: float = 0.0,
    completeness: Literal["complete", "partial", "resource-bounded"] = "complete",
    sensitivity_label: Literal["public", "internal", "confidential", "restricted"] = "internal",
    **kwargs: Any,
) -> ArtifactPointer
```

Writes an artifact to the catalogue. Reads `trace_id`, `run_id`, `agent_id`, and `command` from the current `AgentRunContext`. Returns an `ArtifactPointer`.

Raises `RuntimeError` if no catalogue client is configured. Call `set_catalogue_client()` at startup.

### `set_catalogue_client`

```python
def set_catalogue_client(client: CatalogueClient) -> None
```

Sets the catalogue client for the current context. Called at server startup or in test fixtures.

### `emit_progress`

```python
def emit_progress(data: dict[str, Any]) -> None
```

Emits a progress event for intra-node streaming. Currently a no-op -- will be wired to LangGraph's `get_stream_writer()` when orchestration graphs are built.

## Exceptions

### `NeedsHumanReview`

```python
class NeedsHumanReview(Exception):
    def __init__(self, reason: str = "") -> None
```

Agent requests human review. Sets `signals.needs_human_review = True`. Partial artifacts already written are preserved in `AgentResult.artifacts`.

### `EscalationRequired`

```python
class EscalationRequired(Exception):
    def __init__(self, reason: str = "") -> None
```

Agent has hit a capability or permissions boundary. Sets `signals.escalation_requested = True`.

### `SemanticError`

```python
class SemanticError(Exception):
    def __init__(self, type: str = "unknown", message: str = "") -> None
```

Soft failure. Sets `signals.semantic_error` with the given `type` and `message`. Unexpected exceptions are wrapped as `SemanticError(type="unexpected_error")`.
