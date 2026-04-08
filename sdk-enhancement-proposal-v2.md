# SDK Enhancement Proposal v2

Revised from v1 after critique against Mario Zechner and Toyota design principles.
The signal vocabulary and adapter layer are the right ideas. The `Agent`/`App` class
hierarchy and the `.on()` handler pattern are replaced with smaller primitives that
compose correctly without ceremony.

---

## What changed and why

v1 introduced `Agent` and `App` as explicit registration containers modelled on
FastAPI/Click. The critique: they reintroduce the `register_*` factory pattern that
the existing `decisions.md` already rejected. `app.include_agent(researcher)` is
`register_researcher()` with different packaging. The failure mode is identical — if
the call is missing, the agent silently does not exist and you find out at runtime.

Toyota pull principle: the system should take what it needs when it needs it. The
existing `@agent` decorator does this — registration happens at decoration time, which
is import time. Nothing needs to be called at startup. The enhancement should preserve
this property, not break it.

Mario minimal-interface principle: build only what you need. `Agent` and `App` are
two new classes, two new files, a new contextvar, a new event emitter dependency
(`pyee`), and a snapshot-timing subtlety for `.on()` handlers. The same capability
can be expressed in one `isinstance` check inside the existing `agent()` function.

The signal vocabulary, signal groups, adapter layer, and routing rewrite are all
correct. They are preserved verbatim. The changes are confined to registration,
event observation, and startup validation.

---

## 1. Registration — agent() dual call signature replaces Agent/App

### The problem v1 was solving

When an agent has multiple commands, repeating `@agent(agent_id="researcher", command=...)`
on every function is noisy and error-prone. The agent ID should be stated once.

### The v2 solution

`agent()` detects whether it is called with a string and returns a bound partial in
that case. One `isinstance` check. No new function name, no new concept.

```python
# sdk/_decorator.py

import functools

def agent(agent_id_or_fn=None, *, agent_id: str = None, command: str = "fast"):
    """
    Two call signatures:

    1. researcher = agent("researcher")
       Returns a decorator factory bound to agent_id.
       @researcher(command="deep") then registers a command.

    2. @agent(agent_id="researcher", command="deep")
       Original form — still works, not deprecated.

    Both produce identical registry entries.
    Registration happens at decoration time (import time).
    No mount call required. No startup ceremony.
    """
    if isinstance(agent_id_or_fn, str):
        # researcher = agent("researcher")
        return functools.partial(agent, agent_id=agent_id_or_fn)
    # existing decorator behaviour unchanged
    ...
```

### Usage — named agent with multiple commands

```python
# agents/researcher.py

from sdk import agent, emit_signal, write_artifact
from sdk.signals import SignalType
from sdk.adapters.cli import CLIAdapter
from sdk.parsers import JSONLineParser

researcher = agent("researcher")

@researcher(command="fast")
async def researcher_fast(task: str):
    return await CLIAdapter(
        cmd=["./researcher", "--task", task, "--mode", "fast"],
        parser=JSONLineParser(),
    ).run()

@researcher(command="deep")
async def researcher_deep(task: str, context: list):
    return await CLIAdapter(
        cmd=["./researcher", "--task", task, "--mode", "deep"],
        parser=JSONLineParser(),
    ).run()
```

### Usage — original form still works

```python
# both of these produce identical registrations
researcher = agent("researcher")

@researcher(command="deep")
async def researcher_deep(task: str): ...

# equivalent
@agent(agent_id="researcher", command="deep")
async def researcher_deep(task: str): ...
```

### app.py — import triggers registration

```python
import monet.agents           # registers all reference agents at import time
import agents.researcher      # registers researcher/fast and researcher/deep
import agents.email_agent     # registers email-agent/fast
import agents.rust_agent      # registers rust-researcher/fast
```

No `App` class. No `include_agent` call. The registry is the app, as it always was.

If a developer needs to inspect what is registered, `default_registry` is available:

```python
from monet._registry import default_registry
registered = list(default_registry.all())   # [(agent_id, command), ...]
```

---

## 2. Event observation — on_progress on invoke_agent() replaces .on()

### The problem v1 was solving

Developers want to react to agent events — forward to a webhook, log them, update a
UI — without coupling that logic into the agent function itself. The `.on()` handler
pattern on `Agent` was the proposed solution.

### Why .on() does not fit

Handlers belong to invocations, not to agent definitions. A researcher agent should
not know or care whether its progress events are being forwarded to a webhook. That
decision belongs to whoever is calling the agent. The observation point should be at
`invoke_agent()`, not at decoration time.

The `.on()` pattern also has a snapshot-timing problem: handlers registered after
the `@researcher(command=...)` decorator but before `app.include_agent()` are
captured; handlers registered after are not. This is invisible in the API surface
and produces silent failures.

### The v2 solution

`invoke_agent()` accepts an `on_progress` parameter. It is wired into a contextvar
before the agent executes and read by `emit_progress()` alongside the LangGraph
stream writer.

```python
# sdk/orchestration/_invoke.py

async def invoke_agent(
    agent_id: str,
    command: str = "fast",
    task: str = "",
    context: list | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    skills: list[str] | None = None,
    on_progress: Callable[[dict], None] | list[Callable[[dict], None]] | None = None,
    **kwargs,
) -> AgentResult:
    """
    on_progress: optional callable or list of callables.
    Called synchronously for every emit_progress() call made during this invocation.
    Handler receives the data dict. Async handlers are awaited if the event loop is
    running; sync handlers are called directly.
    Handlers are scoped to this invocation only — concurrent calls are fully isolated.
    """
    handlers = _normalise_handlers(on_progress)
    token = _progress_handlers.set(handlers)
    try:
        # existing dispatch logic unchanged
        ...
    finally:
        _progress_handlers.reset(token)
```

```python
# sdk/_stubs.py

_progress_handlers: ContextVar[list[Callable]] = ContextVar(
    "_progress_handlers", default=[]
)

def emit_progress(data: dict) -> None:
    """
    1. Calls all on_progress handlers registered for the current invocation.
    2. Forwards to LangGraph custom stream if inside a LangGraph execution context.
    No-op for either path if the context is not set.
    """
    # invoke-scoped handlers
    try:
        handlers = _progress_handlers.get()
        for handler in handlers:
            if asyncio.iscoroutinefunction(handler):
                asyncio.get_event_loop().create_task(handler(data))
            else:
                handler(data)
    except LookupError:
        pass

    # LangGraph stream
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()(data)
    except LookupError:
        pass
    # no bare except — real errors surface
```

### Usage

```python
# orchestration node — attach a webhook handler for this invocation only
result = await invoke_agent(
    "researcher",
    command="deep",
    task=task,
    run_id=run_id,
    on_progress=webhook_handler("https://analytics.internal/events"),
)

# multiple handlers
result = await invoke_agent(
    "email-agent",
    command="fast",
    task=task,
    run_id=run_id,
    on_progress=[
        log_handler(logger),
        webhook_handler("https://analytics.internal/events"),
    ],
)

# no observation — default behaviour, identical to current SDK
result = await invoke_agent("writer", command="fast", task=task)
```

The agent function is unchanged. `emit_progress` calls inside `researcher_deep` reach
the webhook handler without the agent knowing or caring.

---

## 3. write_artifact() — thin ambient wrapper over get_catalogue().write()

`get_catalogue().write()` already handles context injection — `CatalogueService` reads
`agent_id`, `run_id`, and `trace_id` from the run context internally and stamps them
onto the artifact metadata. The developer never passes those fields manually.

`write_artifact()` is a one-liner convenience that matches the ambient pattern of
`emit_progress` and `emit_signal`. All three follow the same shape: resolve context
automatically, call the underlying primitive, no explicit context threading required.

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
    """
    Thin wrapper over get_catalogue().write().
    Context injection (agent_id, run_id, trace_id) is handled by CatalogueService.
    The returned pointer is appended to AgentResult.artifacts automatically.

    Equivalent to:
        await get_catalogue().write(content=content, ...)

    Use whichever form is more readable at the call site. Both produce identical
    catalogue records and AgentResult.artifacts entries.
    """
    return await get_catalogue().write(
        content=content,
        content_type=content_type,
        summary=summary,
        confidence=confidence,
        completeness=completeness,
        sensitivity_label=sensitivity_label,
    )
```

### Usage

```python
# ambient form — no setup required
pointer = await write_artifact(
    content=report.encode(),
    content_type="text/markdown",
    summary=report[:200],
    confidence=0.85,
)

# equivalent explicit form — same result
catalogue = get_catalogue()
pointer = await catalogue.write(
    content=report.encode(),
    content_type="text/markdown",
    summary=report[:200],
    confidence=0.85,
)
```

`get_catalogue().write()` is canonical. `write_artifact()` is a convenience alias
that completes the ambient function set. Developers choose based on preference, not
correctness — both paths are identical.

---

### Handler factories — unchanged from v1

```python
# sdk/handlers.py

def langgraph_stream_handler() -> Callable:
    """Forwards events to LangGraph custom stream (already done by emit_progress)."""
    from monet._stubs import emit_progress
    def handler(data: dict) -> None:
        emit_progress(data)
    return handler

def webhook_handler(url: str) -> Callable:
    """POSTs events to a webhook URL as JSON."""
    async def handler(data: dict) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=data)
    return handler

def log_handler(logger, level: str = "info") -> Callable:
    """Logs events at the specified level."""
    def handler(data: dict) -> None:
        getattr(logger, level)("agent event: %s", data)
    return handler
```

---

## 3. Signal vocabulary — unchanged from v1

The signal vocabulary expansion and group constants are correct and preserved exactly.
They solve a real problem (routing functions had no shared vocabulary for signal
semantics) and introduce no new surface beyond the `SignalType` enum and four
`frozenset` constants.

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

    # Keep existing
    SEMANTIC_ERROR        = "semantic_error"


BLOCKING = frozenset({
    SignalType.NEEDS_HUMAN_REVIEW,
    SignalType.ESCALATION_REQUIRED,
    SignalType.APPROVAL_REQUIRED,
})
RECOVERABLE = frozenset({
    SignalType.INSUFFICIENT_CONTEXT,
    SignalType.DEPENDENCY_FAILED,
    SignalType.RATE_LIMITED,
    SignalType.TOOL_UNAVAILABLE,
})
INFORMATIONAL = frozenset({
    SignalType.LOW_CONFIDENCE,
    SignalType.PARTIAL_RESULT,
    SignalType.CONFLICTING_SOURCES,
    SignalType.REVISION_SUGGESTED,
})
AUDIT = frozenset({
    SignalType.EXTERNAL_ACTION_TAKEN,
    SignalType.CONTENT_OFFLOADED,
    SignalType.SENSITIVE_CONTENT,
})
ROUTING = BLOCKING | RECOVERABLE
```

Signal vocabulary and routing consumers (`collect_wave`, `route_after_reflection`) must
land in the same commit. Shipping new `SignalType` members without updating the routing
functions that consume them is dead code.

### emit_signal signature

v1 proposed changing `emit_signal(signal: Signal)` to `emit_signal(type, reason, metadata)`.
This is a breaking change to every existing call site for a cosmetic ergonomic improvement.
It is not worth it. The existing signature stays:

```python
emit_signal(Signal(
    type=SignalType.LOW_CONFIDENCE,
    reason="fewer than 3 sources found",
    metadata={"count": len(sources)},
))
```

If this is genuinely painful in practice, add a keyword-argument convenience wrapper in
a subsequent release with a deprecation path. Do not break existing call sites now.

---

## 4. Adapter layer — unchanged from v1, one fix

The adapter layer is correct. `CLIAdapter`, `SSEAdapter`, `JSONLineParser`, and the
materialisation sequence are preserved from v1.

One fix from the rebuttal: the adapter buffers signals and validates them after process
exit. Unknown signal types from a CLI binary should fail loud. The validation is already
present in v1's `CLIAdapter`. Keep it. Document the known limitation explicitly.

```python
# sdk/adapters/cli.py

class CLIAdapter:
    """
    Wraps a CLI binary. Reads stdout as newline-delimited JSON.
    Translates the event stream into emit_progress / emit_signal / get_catalogue().write()
    calls, which feed the SDK primitive layer identically to a native Python agent.

    Known limitation: if the binary emits a BLOCKING signal and continues running,
    artifacts produced after that point will still be materialised after process exit.
    For irreversible actions, the binary should exit immediately after emitting the
    blocking signal. This will be addressed in a future iteration.
    """

    def __init__(self, cmd: list[str], parser: "OutputParser"):
        self.cmd = cmd
        self.parser = parser

    async def run(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async for line in proc.stdout:
            decoded = line.decode().rstrip()
            if decoded:
                event_type, data = self.parser.parse(decoded)
                if event_type == "progress":
                    emit_progress(data)

        await proc.wait()

        if proc.returncode != 0:
            stderr = (await proc.stderr.read()).decode()
            raise SemanticError(
                type="subprocess_error",
                message=f"CLI exited {proc.returncode}: {stderr[:300]}",
            )

        # validate signal types before materialising anything
        for sig_data in self.parser.pending_signals():
            try:
                SignalType(sig_data["signal_type"])
            except (ValueError, KeyError) as exc:
                raise ValueError(
                    f"Unknown signal type '{sig_data.get('signal_type')}' from CLI agent. "
                    f"Valid types: {[s.value for s in SignalType]}"
                ) from exc

        # materialise artifacts
        catalogue = get_catalogue()
        for artifact_data in self.parser.pending_artifacts():
            await catalogue.write(
                content=artifact_data["content"].encode(),
                content_type=artifact_data.get("content_type", "text/plain"),
                summary=artifact_data.get("summary", ""),
                confidence=float(artifact_data.get("confidence", 0.0)),
                completeness=artifact_data.get("completeness", "complete"),
            )

        # materialise signals
        for sig_data in self.parser.pending_signals():
            emit_signal(Signal(
                type=SignalType(sig_data["signal_type"]),
                reason=sig_data.get("reason", ""),
                metadata=sig_data.get("metadata"),
            ))

        return self.parser.result()
```

The Rust binary protocol and `JSONLineParser` are unchanged from v1.

---

## 5. Routing rewrite — unchanged from v1

`collect_wave` and `route_after_reflection` rewrite is correct and preserved. Must
ship in the same commit as the signal vocabulary expansion.

```python
# graphs/execution.py

from sdk.signals import BLOCKING, RECOVERABLE, INFORMATIONAL, AUDIT, SignalType

async def collect_wave(state: ExecutionState) -> dict[str, Any]:
    current_phase = state["current_phase_index"]
    current_wave  = state["current_wave_index"]

    current_results = [
        r for r in state.get("wave_results", [])
        if r.get("phase_index") == current_phase
        and r.get("wave_index") == current_wave
    ]

    def has_any(results: list, group: frozenset) -> bool:
        return any(
            any(s["type"] in group for s in r.get("signals", []))
            for r in results
        )

    return {
        "signals": {
            "has_blocking":       has_any(current_results, BLOCKING),
            "has_recoverable":    has_any(current_results, RECOVERABLE),
            "has_low_confidence": has_any(current_results, {SignalType.LOW_CONFIDENCE}),
            "has_partial":        has_any(current_results, {SignalType.PARTIAL_RESULT}),
            "has_conflicting":    has_any(current_results, {SignalType.CONFLICTING_SOURCES}),
            "audit": [
                s for r in current_results
                for s in r.get("signals", [])
                if s["type"] in AUDIT
            ],
        }
    }


def route_after_reflection(state: ExecutionState) -> str:
    signals        = state.get("signals") or {}
    reflections    = state.get("wave_reflections") or []
    last           = reflections[-1] if reflections else {}
    revision_count = state.get("revision_count", 0)

    if signals.get("has_blocking"):
        return "human_interrupt"

    if signals.get("has_recoverable"):
        if revision_count < MAX_WAVE_RETRIES:
            return "prepare_wave"
        return "human_interrupt"

    if last.get("verdict") == "fail":
        if revision_count < MAX_WAVE_RETRIES:
            return "prepare_wave"
        return "human_interrupt"

    return "advance"
```

---

## 6. Registry validation — fail at the earliest detectable point

The graphs currently resolve agents entirely at runtime. An unregistered agent is
discovered mid-execution, potentially deep into a long-running wave. Two poka-yoke
checks move this failure to the earliest point where it can be detected.

### Fixed agents — checked at build time

`planner/fast`, `planner/plan`, and `qa/fast` are always required. The graphs cannot
function without them. `build_*` functions are called at startup by LangGraph Server,
making them the natural gate.

```python
# sdk/orchestration/_validate.py

from monet._registry import default_registry

def _assert_registered(agent_id: str, command: str) -> None:
    if default_registry.lookup(agent_id, command) is None:
        raise RuntimeError(
            f"Required agent '{agent_id}/{command}' is not registered. "
            f"Import the agent module before building graphs. "
            f"Example: import monet.agents"
        )
```

```python
# sdk/orchestration/entry_graph.py

def build_entry_graph() -> StateGraph:
    _assert_registered("planner", "fast")
    _assert_registered("planner", "plan")
    ...

# sdk/orchestration/execution_graph.py

def build_execution_graph() -> StateGraph:
    _assert_registered("qa", "fast")
    ...
```

LangGraph Server calls these builders at startup. A missing required agent produces
a loud `RuntimeError` with a clear message before any request is served. No silent
failure, no mid-run discovery.

### Dynamic agents — checked at fan_out_wave

The execution graph's `fan_out_wave` assembles `WaveItem` dicts from the planner's
work brief — `agent_id` and `command` are strings that come from an LLM output at
runtime. These cannot be checked at build time. `fan_out_wave` is the earliest point
where the full wave item list is known, and it fires once per wave before any parallel
invocations start.

```python
# sdk/orchestration/execution_graph.py

async def fan_out_wave(state: ExecutionState) -> list[Send]:
    items = _get_wave_items(state)

    for item in items:
        if default_registry.lookup(item["agent_id"], item["command"]) is None:
            raise SemanticError(
                type="agent_not_found",
                message=(
                    f"Agent '{item['agent_id']}/{item['command']}' is not registered. "
                    f"The planner specified an agent that does not exist. "
                    f"Check the work brief or register the missing agent."
                ),
            )

    return [Send("agent_node", item) for item in items]
```

`SemanticError` is caught by the decorator, sets `success=False`, and feeds into the
existing QA reflection and HITL machinery. The failure is contained to the wave
boundary rather than discovered per-item mid-execution.

### Summary of validation points

| Agent type | Check point | Failure mode |
|---|---|---|
| `planner/fast`, `planner/plan` | `build_entry_graph()` at startup | `RuntimeError` — server does not start |
| `qa/fast` | `build_execution_graph()` at startup | `RuntimeError` — server does not start |
| Dynamic agents from work brief | `fan_out_wave` before wave executes | `SemanticError` — wave fails, HITL machinery responds |

---

## 7. Public SDK surface

```python
# Core registration — agent() gains dual call signature; Agent/App removed
from sdk import agent

# Context-aware ambient functions — write_artifact is new
from sdk import emit_progress, emit_signal, write_artifact
from sdk import get_catalogue, get_run_context, get_run_logger

# Fatal exceptions — unchanged
from sdk import NeedsHumanReview, EscalationRequired, SemanticError

# Types — unchanged
from sdk import Signal, SignalType, AgentResult, ArtifactPointer, AgentRunContext

# Signal groups — new
from sdk.signals import BLOCKING, RECOVERABLE, INFORMATIONAL, AUDIT, ROUTING

# Adapters — new, for non-Python agents
from sdk.adapters.cli import CLIAdapter
from sdk.adapters.sse import SSEAdapter

# Parsers — new
from sdk.parsers import JSONLineParser

# Handler factories — new, used with invoke_agent(on_progress=...)
from sdk.handlers import langgraph_stream_handler, webhook_handler, log_handler

# Orchestration
from sdk.orchestration import invoke_agent   # on_progress parameter is new
```

Removed from v1 surface: `Agent`, `App`, `agent_group` (capability absorbed into `agent()`).

---

## 8. File layout

```
monet/
  app.py                    # import-only — triggers agent registration
  langgraph.json

  agents/
    researcher.py           # agent("researcher"), CLI-backed
    writer.py               # agent("writer"), Python native
    email_agent.py          # agent("email-agent"), SSE-backed
    rust_agent.py           # agent("rust-researcher"), CLI-backed

  graphs/
    entry.py                # build-time registry checks for planner/fast, planner/plan
    planner.py
    execution.py            # build-time check for qa/fast; fan_out_wave dynamic check;
                            # collect_wave and route_after_reflection updated

  sdk/
    __init__.py             # exports agent, emit_*, write_artifact, get_*, exceptions, types
    _decorator.py           # agent() — dual call signature
    _registry.py            # AgentRegistry, registry_scope()
    _context.py             # contextvars, get_run_context(), get_run_logger()
    _catalogue.py           # get_catalogue(), configure_catalogue()
    _stubs.py               # emit_progress(), emit_signal(), write_artifact()  ← updated
    _tracing.py             # configure_tracing(), get_tracer()
    signals.py              # SignalType enum, group frozensets  ← new
    types.py                # AgentRunContext, AgentResult, Signal, ArtifactPointer
    exceptions.py           # NeedsHumanReview, EscalationRequired, SemanticError
    handlers.py             # handler factories  ← new
    parsers.py              # JSONLineParser, OutputParser protocol  ← new
    adapters/
      __init__.py
      cli.py                # CLIAdapter  ← new
      sse.py                # SSEAdapter  ← new
    orchestration/
      __init__.py
      _invoke.py            # invoke_agent() — on_progress parameter added
      _validate.py          # _assert_registered()  ← new
      ...
```

Removed from v1 layout: `sdk/agent.py`, `sdk/app.py`.

---

## 9. Recommended change order

Four independent changes. Each is reviewable and deployable on its own.

**Change A — Signal vocabulary and routing (no breaking changes)**

- Create `sdk/signals.py` with expanded `SignalType` and group frozensets
- Rewrite `collect_wave` and `route_after_reflection` in `graphs/execution.py`
- Ship in one commit. New `SignalType` members without updated routing is dead code.

**Change B — Adapter layer (no breaking changes)**

- `sdk/adapters/cli.py` — `CLIAdapter`
- `sdk/adapters/sse.py` — `SSEAdapter`
- `sdk/parsers.py` — `JSONLineParser`, `OutputParser` protocol
- `sdk/handlers.py` — handler factories
- Can land before or after Change A.

**Change C — agent() dual signature, write_artifact, on_progress (additive, no breaking changes)**

- Update `agent()` in `sdk/_decorator.py` with `isinstance` dual-signature check
- Add `write_artifact()` to `sdk/_stubs.py`
- Add `_progress_handlers` contextvar to `sdk/_stubs.py`
- Update `emit_progress()` to call invoke-scoped handlers
- Add `on_progress` parameter to `invoke_agent()` in `sdk/orchestration/_invoke.py`
- Update `sdk/__init__.py` to export `write_artifact`
- Can land before or after A and B.

**Change D — Registry validation (no breaking changes)**

- Add `sdk/orchestration/_validate.py` with `_assert_registered()`
- Add build-time checks to `build_entry_graph()` and `build_execution_graph()`
- Add dynamic check to `fan_out_wave()`
- Depends on Change A (needs `SemanticError` handling in routing). Otherwise independent.

None of the four changes remove existing API. The verbose `@agent(agent_id=..., command=...)`
form continues to work unchanged. Migration to `researcher = agent("researcher")` is optional.

---

## Design principles applied

Registration at import time, not startup time. `agent()` with a string argument is
`functools.partial`. Decoration is registration. No mount call to forget.

Observation at invocation time, not definition time. `on_progress` on `invoke_agent`
means handlers belong to the caller, not the agent. The agent has no knowledge of
how its progress events are consumed.

Minimal surface. The dual call signature is one `isinstance` check. `write_artifact`
is one line. `on_progress` is one new parameter and one new contextvar. No new classes,
no new dependencies, no snapshot-timing subtleties.

Pull not push. The registry pulls agents in at import time. `invoke_agent` pulls
handlers in at call time. Nothing is pushed into a container at startup.

Fail loud at the earliest detectable point. Fixed agents fail at startup via build-time
checks. Dynamic agents fail at wave boundary via `fan_out_wave`. Unknown signal types
from CLI agents fail before materialisation. Missing catalogue fails at the first
`write()` call.

Jidoka. The blocking-signal-mid-stream limitation in `CLIAdapter` is a known defect,
not a known limitation. It must be addressed before the adapter is recommended for
agents that perform irreversible actions.
