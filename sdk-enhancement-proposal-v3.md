# SDK Enhancement Proposal v2

Revised from v1 after design critique and extended discussion. The core changes:
`Agent`/`App` class hierarchy replaced with a dual-signature `agent()` function.
`.on()` handler pattern moved from agent definition to `AgentStream`, which is the
correct owner. `AgentResult.output` type widened to reflect what agents actually
return. `on_progress` on `invoke_agent` dropped — two places to define handlers is
one too many. Registry validation added at the earliest detectable point per jidoka.

Design draws on three precedents: Prefect (`@flow` stores `.fn`, engine calls it),
FastAPI (route function describes what happens, framework calls it), Click (command
function is called by the framework, not itself). The decorator is the engine. The
function body is the description. The stream is the event bus.

---

## What changed from v1 and why

`Agent` and `App` are removed. They reintroduce the `register_*` factory pattern
that `decisions.md` already rejected. `app.include_agent(researcher)` is
`register_researcher()` with different packaging — same silent failure mode if the
call is missing.

`.on()` on `Agent` is removed. Handlers registered on an agent definition create
action at a distance between where the handler is defined and where it fires. The
correct owner of event handlers is the stream, at the point of use inside the
function body.

`on_progress` on `invoke_agent` is dropped. It solves a real problem but creates
two places to define handlers — on the stream inside the function, and on
`invoke_agent` outside it. One place per concern.

`CLIAdapter`, `SSEAdapter` as passive parsers are replaced by `AgentStream` — an
active async iterator with named constructors per transport and `.on()` handler
registration. The stream is the event bus between an external agent and the SDK
primitive layer.

`AgentResult.output: str | ArtifactPointer | None` is widened. Stream-based agents
produce multiple artifacts, not one. Some agents return structured data. The type
restriction was too narrow for real use.

---

## 1. Decorator — registration and context injection only

The decorator has two jobs and two jobs only.

First, registration. `@researcher(command="fast")` is syntactic sugar over
`default_registry.register("researcher", "fast", fn)`. Registration happens at
decoration time, which is import time. No startup call required.

Second, context injection. Before calling the function, the decorator sets
contextvars so that `emit_signal`, `emit_progress`, `write_artifact`,
`get_run_context()`, and `get_run_logger()` all work anywhere in the call stack
beneath the decorated function, including inside `AgentStream.run()`.

The decorator does not make execution decisions. It does not detect return types.
It does not know about transports. It calls the function, awaits the result if the
function is async, wraps the result into `AgentResult`, and handles exceptions.
That is all.

### Dual call signature

`agent()` detects whether it is called with a string and returns a bound partial.
One `isinstance` check. No new function, no new concept.

```python
# sdk/_decorator.py

def agent(agent_id_or_fn=None, *, agent_id: str = None, command: str = "fast"):
    """
    Two call signatures:

    1. researcher = agent("researcher")
       Returns a decorator factory bound to agent_id.
       @researcher(command="deep") then registers a command.

    2. @agent(agent_id="researcher", command="deep")
       Original verbose form — still works, not deprecated.

    Both produce identical registry entries.
    Registration happens at decoration time (import time).
    """
    if isinstance(agent_id_or_fn, str):
        return functools.partial(agent, agent_id=agent_id_or_fn)
    # existing decorator behaviour unchanged
    ...
```

### Decorator inner loop

```python
# sdk/_decorator.py — _build_wrapper internals

async def wrapper(ctx: AgentRunContext) -> AgentResult:
    artifacts = []
    signals   = []

    ctx_token = _run_context.set(ctx)
    sig_token = _signal_collector.set(signals)
    art_token = _artifact_collector.set(artifacts)

    try:
        with tracer.start_as_current_span(f"agent.{agent_id}.{command}"):
            try:
                kwargs = _inject_params(fn, ctx)
                result = await fn(**kwargs) if iscoroutinefunction(fn) else fn(**kwargs)
                return _wrap_result(result, ctx, artifacts, signals)
            except (NeedsHumanReview, EscalationRequired, SemanticError) as exc:
                return _handle_exception(exc, ctx, artifacts, signals)
    finally:
        _run_context.reset(ctx_token)
        _signal_collector.reset(sig_token)
        _artifact_collector.reset(art_token)
```

No `isinstance(result, AgentTransport)` check. No transport detection. The function
is responsible for running the stream and returning a value. The decorator wraps
whatever comes back.

### Python-native agent — unchanged

```python
researcher = agent("researcher")

@researcher(command="fast")
async def researcher_fast(task: str, context: list):
    sources = await fetch_sources(task)
    if len(sources) < 3:
        emit_signal(Signal(
            type=SignalType.LOW_CONFIDENCE,
            reason="fewer than 3 sources",
            metadata={"count": len(sources)},
        ))
    report = await synthesise(sources)
    return await write_artifact(
        content=report.encode(),
        content_type="text/markdown",
        summary=report[:200],
        confidence=0.85,
    )
```

### External agent — stream-based

```python
@researcher(command="deep")
async def researcher_deep(task: str, context: list):
    await (
        AgentStream
        .cli(cmd=["./researcher", "--task", task, "--mode", "deep"])
        .on("progress", emit_progress)
        .on("artifact", write_artifact)
        .on("signal",   emit_signal)
        .run()
    )
```

The function is async. It awaits `.run()`. The decorator does not know about
`AgentStream`. The contextvars are set before the function is called, so
`write_artifact`, `emit_signal`, and `emit_progress` all resolve correctly inside
`.run()`.

### Registration via import

```python
# app.py — import triggers registration at decoration time

import monet.agents       # registers all reference agents
import agents.researcher  # registers researcher/fast and researcher/deep
import agents.writer      # registers writer/fast
```

No `App` class. No `include_agent`. The registry is the app.

---

## 2. AgentStream — typed async event bus for external agents

`AgentStream` is the translation boundary between an external agent's output and
the SDK primitive layer. It reads a stream — stdout, SSE, HTTP polling — parses
typed JSON events, and fires registered handlers for each event type as events
arrive. It is an active async iterator, not a passive parser.

The `.on()` builder registers handlers at the point of use inside the function
body. Scope is explicit: these handlers fire for this stream in this invocation.
No action at a distance. No snapshot-timing subtleties.

### Named constructors per transport

```python
AgentStream.cli(cmd: list[str], **kwargs)              # subprocess stdout
AgentStream.sse(url: str, **kwargs)                    # HTTP SSE stream
AgentStream.http(url: str, interval: float, **kwargs)  # HTTP polling
AgentStream.grpc(stub, method: str, **kwargs)          # gRPC — see limitations
```

All return the same `AgentStream` instance. All support the same `.on()` interface.
Transport type is a detail of construction, not of consumption.

### Handler registration — .on() builder

```python
await (
    AgentStream.cli(cmd=["./researcher", "--task", task])
    .on("progress", emit_progress)
    .on("artifact", write_artifact)
    .on("signal",   emit_signal)
    .on("error",    log_handler(logger, level="error"))
    .run()
)
```

`.on()` returns `self`. Multiple handlers per event type are called in registration
order. Each handler receives the full event dict. Async handlers are awaited. Sync
handlers are called directly.

### Default handler wiring

If no handlers are registered, `AgentStream.run()` applies sensible defaults:

| Event type | Default handler |
|---|---|
| `progress` | `emit_progress(data)` |
| `signal` | `emit_signal(Signal(...))` |
| `artifact` | `write_artifact(...)` |
| `error` | `raise SemanticError(...)` |
| `result` | captured as return value of `.run()` |
| unknown | log warning, continue |

Developers register `.on()` handlers only when they want non-default behaviour.
The minimal integration is genuinely minimal:

```python
@researcher(command="fast")
async def researcher_fast(task: str):
    await AgentStream.cli(cmd=["./researcher", "--task", task]).run()
```

Four lines including the decorator. Defaults handle everything.

### What .run() returns

`.run()` returns `str | None` — the value from the last `result` event emitted by
the external agent, or `None` if no result event was emitted.

All artifacts are already in the catalogue and in the artifact collector by the time
`.run()` returns — the default `artifact` handler called `write_artifact` for each
one as it arrived. The return value of `.run()` is secondary for stream-based agents.
The primary output is in `AgentResult.artifacts`.

```python
@researcher(command="deep")
async def researcher_deep(task: str):
    result_str = await AgentStream.cli(cmd=["./researcher", "--task", task]).run()
    # result_str is the "output" field from the last result event, or None
    # all artifacts are already collected — AgentResult.artifacts is populated
    return result_str  # can return None if artifacts are the primary output
```

### Event protocol

Newline-delimited JSON to stdout (CLI) or SSE data fields (HTTP). Fixed and
versioned. Unknown signal types raise `ValueError` before any handlers fire — a
version mismatch between the binary and the SDK is always a loud failure.

```json
{"type": "progress", "status": "fetching sources", "done": 3, "total": 10}

{"type": "signal",
 "signal_type": "low_confidence",
 "reason": "only 2 sources found",
 "metadata": {"count": 2}}

{"type": "artifact",
 "content_type": "text/markdown",
 "summary": "Research findings on X",
 "confidence": 0.85,
 "completeness": "complete",
 "content": "...full content..."}

{"type": "result", "output": "research complete"}

{"type": "error",
 "error_type": "tool_unavailable",
 "message": "search API returned 503"}
```

### Advanced handler usage

The `.on()` interface is where monet integrates with external services. Handlers
can pipe artifacts to rendering services, map custom event types to signals, feed
analytics, or forward events to webhooks. Multiple handlers per event type compose
cleanly.

```python
@researcher(command="deep")
async def researcher_deep(task: str):
    await (
        AgentStream.cli(cmd=["./researcher", "--task", task, "--mode", "deep"])
        .on("progress", emit_progress)
        .on("signal",   emit_signal)
        # artifacts go to catalogue AND to an external rendering service
        .on("artifact", write_artifact)
        .on("artifact", webhook_handler("https://renderer.internal/artifacts"))
        # custom event type from this specific binary
        .on("citation", lambda d: emit_progress({"type": "citation", **d}))
        # errors go to alerting
        .on("error",    log_handler(logger, level="error"))
        .run()
    )
```

### Python Click CLI example

The external agent is a standalone Click CLI. It has no monet dependency — it just
writes JSON lines to stdout. The stream handles the rest.

```python
# researcher_cli.py — standalone Click CLI, no monet dependency

import json
import click

@click.command()
@click.option("--task", required=True)
@click.option("--mode", default="fast")
def main(task: str, mode: str):
    # progress
    print(json.dumps({"type": "progress", "status": "fetching sources", "done": 0, "total": 10}), flush=True)

    sources = fetch_sources(task, mode)

    if len(sources) < 3:
        print(json.dumps({
            "type": "signal",
            "signal_type": "low_confidence",
            "reason": "fewer than 3 sources found",
            "metadata": {"count": len(sources)},
        }), flush=True)

    report = synthesise(sources)

    print(json.dumps({
        "type": "artifact",
        "content_type": "text/markdown",
        "summary": report[:200],
        "confidence": 0.85,
        "completeness": "complete",
        "content": report,
    }), flush=True)

    print(json.dumps({"type": "result", "output": "research complete"}), flush=True)

if __name__ == "__main__":
    main()
```

The monet agent wrapper invokes it via `AgentStream.cli()`:

```python
# agents/researcher.py

researcher = agent("researcher")

@researcher(command="fast")
async def researcher_fast(task: str):
    await AgentStream.cli(cmd=["python", "researcher_cli.py", "--task", task]).run()

@researcher(command="deep")
async def researcher_deep(task: str):
    await (
        AgentStream.cli(cmd=["python", "researcher_cli.py", "--task", task, "--mode", "deep"])
        .on("artifact", webhook_handler("https://renderer.internal/artifacts"))
        .on("error",    log_handler(logger, level="error"))
        .run()
    )
```

### gRPC

`AgentStream.grpc()` is reserved. Full implementation requires resolving channel
lifecycle (shared, not per-invocation) and bidirectional streaming semantics that
the current `.run()` model does not support. Developers with gRPC agents subclass
`AgentStream` and implement `_iter_events()`:

```python
class MyGRPCStream(AgentStream):
    async def _iter_events(self):
        async for response in self.stub.StreamResearch(self.request):
            yield {"type": "artifact", "content": response.content, ...}

@researcher(command="fast")
async def researcher_fast(task: str):
    await (
        MyGRPCStream(stub=channel.stub, request=Request(task=task))
        .on("artifact", write_artifact)
        .run()
    )
```

---

## 3. AgentResult — widened output type

`AgentResult.output: str | ArtifactPointer | None` is too narrow. Stream-based
agents produce multiple artifacts. Some agents return structured data. Some agents
return nothing because the primary output is already in `artifacts`.

```python
@dataclass(frozen=True)
class AgentResult:
    success: bool
    output: str | dict | None = None
    artifacts: list[ArtifactPointer] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    trace_id: str = ""
    run_id: str = ""

    def has_signal(self, signal_type: SignalType) -> bool: ...
    def get_signal(self, signal_type: SignalType) -> Signal | None: ...
```

`output` is for inline results only — a string summary, a structured dict, a triage
decision. Things small enough to live in state and be read directly without a
catalogue fetch.

`artifacts` is for everything written to the catalogue. Every `ArtifactPointer`
ends up here regardless of whether the agent is Python-native or stream-based. A
Python-native agent that calls `write_artifact()` does not need to return the
pointer — the decorator's artifact collector captures it automatically. The agent
returns `None` or an inline string and `artifacts` is populated by the collector.

The two fields have distinct roles and the types enforce that. The orchestrator
never needs to check both fields for the same kind of thing. `WaveResult` drops its
`output or (artifacts[0] if artifacts else None)` fallback — if the orchestrator
needs a catalogue reference it reads `artifacts[0]` explicitly.

---

## 4. Ambient functions — unchanged contract, write_artifact added

Three ambient functions, uniform pattern: resolve context from contextvars set by
the decorator, call the underlying primitive. All three work anywhere in the call
stack beneath `@agent`, including inside `AgentStream.run()`.

```python
emit_progress(data: dict) -> None
    # writes to LangGraph stream writer
    # no-op outside LangGraph execution context

emit_signal(signal: Signal) -> None
    # appends to _signal_collector set by decorator
    # RuntimeError if called outside decorator context

write_artifact(content, content_type, summary, ...) -> ArtifactPointer
    # thin wrapper over get_catalogue().write()
    # appends to _artifact_collector set by decorator
    # context stamping (agent_id, run_id, trace_id) handled by CatalogueService
    # RuntimeError if called outside decorator context
```

`get_catalogue().write()` is canonical. `write_artifact()` is a convenience alias
that completes the ambient trio. Both produce identical `AgentResult.artifacts`
entries and identical catalogue records.

```python
# sdk/_stubs.py

async def write_artifact(
    content: bytes,
    content_type: str,
    summary: str,
    confidence: float = 0.0,
    completeness: str = "complete",
    sensitivity_label: str = "internal",
) -> ArtifactPointer:
    return await get_catalogue().write(
        content=content,
        content_type=content_type,
        summary=summary,
        confidence=confidence,
        completeness=completeness,
        sensitivity_label=sensitivity_label,
    )
```

`emit_progress` reverts to its original simple form. No `_progress_handlers`
contextvar. The LangGraph stream writer is the only target:

```python
def emit_progress(data: dict) -> None:
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()(data)
    except LookupError:
        pass
    # no bare except — real errors surface
```

Handler registration for progress events belongs on `AgentStream.on("progress", ...)`,
not on `emit_progress` itself.

---

## 5. Signal vocabulary and routing groups

Signal vocabulary expansion and routing rewrite ship in one commit. New `SignalType`
members without updated routing consumers is dead code.

```python
# sdk/signals.py

class SignalType(StrEnum):
    # Control flow — orchestrator routes on these directly
    NEEDS_HUMAN_REVIEW    = "needs_human_review"
    ESCALATION_REQUIRED   = "escalation_required"
    APPROVAL_REQUIRED     = "approval_required"
    INSUFFICIENT_CONTEXT  = "insufficient_context"
    DEPENDENCY_FAILED     = "dependency_failed"
    RATE_LIMITED          = "rate_limited"
    TOOL_UNAVAILABLE      = "tool_unavailable"

    # Informational — feeds QA reflection verdict, not direct routing
    LOW_CONFIDENCE        = "low_confidence"
    PARTIAL_RESULT        = "partial_result"
    CONFLICTING_SOURCES   = "conflicting_sources"
    REVISION_SUGGESTED    = "revision_suggested"

    # Audit — recorded in state, no routing consequence
    EXTERNAL_ACTION_TAKEN = "external_action_taken"
    CONTENT_OFFLOADED     = "content_offloaded"
    SENSITIVE_CONTENT     = "sensitive_content"

    SEMANTIC_ERROR        = "semantic_error"


BLOCKING      = frozenset({SignalType.NEEDS_HUMAN_REVIEW,
                            SignalType.ESCALATION_REQUIRED,
                            SignalType.APPROVAL_REQUIRED})
RECOVERABLE   = frozenset({SignalType.INSUFFICIENT_CONTEXT,
                            SignalType.DEPENDENCY_FAILED,
                            SignalType.RATE_LIMITED,
                            SignalType.TOOL_UNAVAILABLE})
INFORMATIONAL = frozenset({SignalType.LOW_CONFIDENCE,
                            SignalType.PARTIAL_RESULT,
                            SignalType.CONFLICTING_SOURCES,
                            SignalType.REVISION_SUGGESTED})
AUDIT         = frozenset({SignalType.EXTERNAL_ACTION_TAKEN,
                            SignalType.CONTENT_OFFLOADED,
                            SignalType.SENSITIVE_CONTENT})
ROUTING       = BLOCKING | RECOVERABLE
```

`collect_wave` and `route_after_reflection` read from group frozensets, never from
raw string matching.

---

## 6. Registry validation — fail at the earliest detectable point

### Fixed agents — checked at build time (poka-yoke)

```python
# sdk/orchestration/_validate.py

def _assert_registered(agent_id: str, command: str) -> None:
    if default_registry.lookup(agent_id, command) is None:
        raise RuntimeError(
            f"Required agent '{agent_id}/{command}' is not registered. "
            f"Import the agent module before building graphs. "
            f"Example: import monet.agents"
        )

# entry_graph.py
def build_entry_graph() -> StateGraph:
    _assert_registered("planner", "fast")
    _assert_registered("planner", "plan")
    ...

# execution_graph.py
def build_execution_graph() -> StateGraph:
    _assert_registered("qa", "fast")
    ...
```

LangGraph Server calls builders at startup. Missing required agents produce a loud
`RuntimeError` before any request is served.

### Dynamic agents — checked at fan_out_wave (jidoka)

```python
async def fan_out_wave(state: ExecutionState) -> list[Send]:
    items = _get_wave_items(state)
    for item in items:
        if default_registry.lookup(item["agent_id"], item["command"]) is None:
            raise SemanticError(
                type="agent_not_found",
                message=(
                    f"Agent '{item['agent_id']}/{item['command']}' is not registered. "
                    f"The planner specified an agent that does not exist."
                ),
            )
    return [Send("agent_node", item) for item in items]
```

| Agent | Check point | Failure |
|---|---|---|
| `planner/fast`, `planner/plan` | `build_entry_graph()` at startup | `RuntimeError` — server does not start |
| `qa/fast` | `build_execution_graph()` at startup | `RuntimeError` — server does not start |
| Dynamic agents from work brief | `fan_out_wave` | `SemanticError` — HITL responds |

---

## 7. How AgentStream.run() integrates with LangGraph

```
LangGraph calls agent_node(item: WaveItem)
    → invoke_agent("researcher", command="deep", task=...)
        → decorator sets _run_context, _signal_collector, _artifact_collector
            → researcher_deep(task=task)
                → AgentStream.cli(...).on(...).run()
                    → reads stdout line by line
                    → "progress" event → emit_progress() → LangGraph stream
                    → "artifact" event → write_artifact() → catalogue + collector
                    → "signal" event  → emit_signal() → signal collector
                    → "result" event  → captured as return value
                    → returns str | None
            → returns str | None
        → decorator reads collectors, assembles AgentResult
        → resets contextvars
    → returns AgentResult
→ agent_node assembles WaveResult into state
```

From LangGraph's perspective `agent_node` is a node function that takes a `WaveItem`
and returns a dict. Everything beneath is monet's concern.

The contextvars are set before the function is called and live until the decorator
resets them after the function returns. `AgentStream.run()` executes entirely inside
that window, so all ambient functions resolve correctly.

---

## 8. Handler factories

```python
# sdk/handlers.py

def webhook_handler(url: str) -> Callable:
    """POSTs the event dict to a webhook as JSON."""
    async def handler(data: dict) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=data)
    return handler

def log_handler(logger, level: str = "info") -> Callable:
    """Logs the event dict at the specified level."""
    def handler(data: dict) -> None:
        getattr(logger, level)("agent event: %s", data)
    return handler
```

`langgraph_stream_handler` is omitted — `emit_progress` already writes to the
LangGraph stream. A handler that calls `emit_progress` would double-write.

---

## 9. Public SDK surface

```python
# Core registration
from sdk import agent

# Ambient functions
from sdk import emit_progress, emit_signal, write_artifact
from sdk import get_catalogue, get_run_context, get_run_logger

# Fatal exceptions
from sdk import NeedsHumanReview, EscalationRequired, SemanticError

# Types
from sdk import Signal, SignalType, AgentResult, ArtifactPointer, AgentRunContext
# AgentResult.output: str | dict | None — inline results only
# AgentResult.artifacts: list[ArtifactPointer] — all catalogue outputs

# Signal groups
from sdk.signals import BLOCKING, RECOVERABLE, INFORMATIONAL, AUDIT, ROUTING

# Stream — primary integration surface for external agents
from sdk.streams import AgentStream

# Handler factories
from sdk.handlers import webhook_handler, log_handler

# Orchestration
from sdk.orchestration import invoke_agent
```

Removed from v1: `Agent`, `App`, `CLIAdapter`, `SSEAdapter`, `JSONLineParser`,
`PrefixParser`, `langgraph_stream_handler`, `on_progress` on `invoke_agent`.
`AgentStream` replaces adapters and parsers as a unified stream abstraction.

---

## 10. File layout

```
monet/
  app.py                    # import-only — triggers agent registration

  agents/
    researcher.py           # agent("researcher") — CLI-backed via AgentStream
    researcher_cli.py       # standalone Click CLI, no monet dependency
    writer.py               # agent("writer") — Python native
    email_agent.py          # agent("email-agent") — SSE-backed via AgentStream

  graphs/
    entry.py                # _assert_registered(planner/fast, planner/plan)
    planner.py
    execution.py            # _assert_registered(qa/fast)
                            # fan_out_wave dynamic check
                            # collect_wave + route_after_reflection updated

  sdk/
    __init__.py
    _decorator.py           # agent() — dual call signature
    _registry.py            # AgentRegistry, registry_scope()
    _context.py             # contextvars, get_run_context(), get_run_logger()
    _catalogue.py           # get_catalogue(), configure_catalogue()
    _stubs.py               # emit_progress(), emit_signal(), write_artifact()
    _tracing.py             # configure_tracing(), get_tracer()
    signals.py              # SignalType, group frozensets  ← new
    types.py                # AgentResult (widened), AgentRunContext, Signal, ArtifactPointer
    exceptions.py           # NeedsHumanReview, EscalationRequired, SemanticError
    handlers.py             # webhook_handler, log_handler  ← new
    streams.py              # AgentStream, named constructors, .on(), .run()  ← new
    orchestration/
      __init__.py
      _invoke.py            # invoke_agent() — unchanged except AgentResult widening
      _validate.py          # _assert_registered()  ← new
      ...
```

---

## 11. Recommended change order

**Change A — Signal vocabulary and routing**
- `sdk/signals.py` — `SignalType` expansion, group frozensets
- `graphs/execution.py` — `collect_wave`, `route_after_reflection` rewrite
- One commit. Vocabulary without routing consumers is dead code.

**Change B — AgentStream**
- `sdk/streams.py` — `AgentStream`, named constructors, `.on()`, `.run()`
- `sdk/handlers.py` — `webhook_handler`, `log_handler`
- Independent of A.

**Change C — agent() dual signature and write_artifact**
- `sdk/_decorator.py` — `isinstance` check for dual signature
- `sdk/_stubs.py` — `write_artifact()`, `emit_progress()` simplified
- `sdk/__init__.py` — export `write_artifact`
- Independent of A and B.

**Change D — AgentResult output type correction**
- `sdk/types.py` — `output: str | dict | None` — remove `ArtifactPointer` and `list[ArtifactPointer]`
- `sdk/orchestration/_state.py` — remove `output or (artifacts[0] if artifacts else None)` fallback from `WaveResult` handling; orchestrator reads `output` and `artifacts` as distinct fields
- Independent of A, B, C.

**Change E — Registry validation**
- `sdk/orchestration/_validate.py` — `_assert_registered()`
- Build-time checks in `entry_graph.py` and `execution_graph.py`
- `fan_out_wave` dynamic check in `execution_graph.py`
- Depends on A (needs `SemanticError` in routing). Otherwise independent.

No change removes existing API. `@agent(agent_id=..., command=...)` continues to
work. Migration to `researcher = agent("researcher")` is optional.

---

## 12. Known limitations

**Blocking signal mid-stream.** If an external binary emits `APPROVAL_REQUIRED`
and continues running, artifacts produced after that point will still be materialised
after process exit. For irreversible actions, the binary should exit immediately after
emitting a blocking signal. This is a known defect. It must be addressed before
`AgentStream` is recommended for agents that perform irreversible actions.

**gRPC.** `AgentStream.grpc()` is a reserved constructor. Full implementation
requires resolving shared channel lifecycle and bidirectional streaming semantics.
Developers with gRPC agents subclass `AgentStream` and implement `_iter_events()`.

---

## 13. Design principles

The decorator is registration and context injection only. It does not make execution
decisions. It does not know about transports.

The stream is the event bus. `AgentStream.run()` executes inside the decorator's
contextvar window. All ambient functions work inside `.run()` without additional
wiring.

Handlers belong to the stream at the point of use. Not to the agent definition.
Not to `invoke_agent`. Scope is explicit — these handlers fire for this stream in
this invocation.

Registration at import time. `agent("researcher")` is `functools.partial`.
Decoration is registration. No mount call to forget.

Fail at the earliest detectable point. Fixed agents at build time. Dynamic agents
at `fan_out_wave`. Unknown signal types before materialisation.

The LangGraph stream is the caller observation point. `emit_progress` writes to it.
Developers subscribing via `astream_events` see all progress events from all agents
without additional wiring.