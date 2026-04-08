# SDK Enhancement Proposal

## Overview

This document summarises a proposed enhancement to the monet SDK covering three areas: a decorator-based agent registration pattern modelled on FastAPI and Click, a standardised signal vocabulary for orchestration control flow, and context-aware ambient functions for emitting events, signals, and artifacts from anywhere in the call stack.

The goal is a consistent developer experience regardless of whether an agent is implemented in Python, compiled to a CLI binary, or exposed as an HTTP SSE endpoint.

---

## 1. Decorator Pattern — Modelled on FastAPI / Click

### Motivation

FastAPI uses `@app.include_router()` and `@router.get()`. Click uses `@cli.add_command()` and `@group.command()`. Both share the same principle: the intermediate object is created once, owned by its file, and mounted explicitly onto the top-level container. Registration is visible and intentional, not a side effect of importing a file.

The current SDK uses `default_registry` implicitly. The enhancement makes registration explicit.

### Agent as intermediate container

```python
# sdk/agent.py

class Agent:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._commands: dict[str, Callable] = {}

    def __call__(self, command: str = "fast"):
        """@researcher(command="fast") — registers a command."""
        def decorator(fn):
            _validate_signature(fn, self.agent_id)
            wrapper = self._build_wrapper(fn, command)
            self._commands[command] = wrapper
            return wrapper
        return decorator

    def on(self, event_type: str):
        """@researcher.on("progress") — registers an event handler."""
        def decorator(fn):
            self._handlers[event_type].append(fn)
            return fn
        return decorator

    def use(self, event_type: str, handler: Callable) -> "Agent":
        """Non-decorator form of .on() for programmatic registration."""
        self._handlers[event_type].append(handler)
        return self
```

### App as explicit mount point

```python
# sdk/app.py

class App:
    def __init__(self, name: str):
        self.name = name
        self._agents: dict[str, Agent] = {}

    def include_agent(self, agent: Agent) -> None:
        if agent.agent_id in self._agents:
            raise ValueError(f"agent '{agent.agent_id}' already registered")
        self._agents[agent.agent_id] = agent
        for command, wrapper in agent._commands.items():
            default_registry.register(agent.agent_id, command, wrapper)
```

### app.py — single source of truth

```python
# app.py

from sdk import App
from agents.researcher import researcher
from agents.email_agent import email_agent
from agents.rust_agent import rust_agent

app = App("monet")
app.include_agent(researcher)
app.include_agent(email_agent)
app.include_agent(rust_agent)
```

### Agent file layout

Each agent file is self-contained. The agent owns its commands and event handlers. No global registry, no implicit side-effect imports.

```python
# agents/researcher.py

from sdk import Agent, emit_signal, emit_progress
from sdk.types import SignalType
from sdk.adapters.cli import CLIAdapter
from sdk.parsers import JSONLineParser

researcher = Agent("researcher")

@researcher(command="fast")
async def research_fast(task: str):
    await CLIAdapter(
        cmd=["./researcher", "--task", task, "--mode", "fast"],
        parser=JSONLineParser(),
    ).run()

@researcher(command="deep")
async def research_deep(task: str, context: list):
    await CLIAdapter(
        cmd=["./researcher", "--task", task, "--mode", "deep"],
        parser=JSONLineParser(),
    ).run()

@researcher.on("progress")
def on_progress(data):
    emit_progress({"agent": "researcher", **data})

@researcher.on("agent:failed")
def on_failed(data):
    print("researcher failed:", data["error"])
```

### Key design decisions

`_validate_signature` runs at decoration time, not call time. If a parameter name is not a valid `AgentRunContext` field, it raises `TypeError` immediately — not on the first invocation in production.

`app.include_agent()` raises `ValueError` on duplicate registration rather than silently overwriting. Fail loud at startup.

`Agent` does not hold a reference to `App`. Registration happens at mount time via `include_agent`, removing the circular dependency present in the current design.

---

## 2. Context-Aware Ambient Functions

### Motivation

Any function within an agent's call stack should be able to emit progress, signals, and artifacts without receiving a context argument. This mirrors how Python's `logging` module works — `logging.info()` resolves the correct logger from context without requiring an explicit logger parameter.

Implementation uses `ContextVar`, the same mechanism the existing decorator uses for `_signal_collector` and `_artifact_collector`. Each invocation sets its own contextvar token, so concurrent runs of the same agent never share state.

### Three ambient functions

```python
# sdk/_context.py

def emit_progress(data: dict) -> None:
    """
    Forward to LangGraph custom stream. No-op outside LangGraph context
    so agents remain testable without a running LangGraph server.
    """
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()(data)
    except Exception:
        pass


def emit_signal(
    type: SignalType,
    reason: str,
    metadata: dict | None = None,
) -> None:
    """
    Append a non-fatal signal to AgentResult.signals.
    Signals accumulate — multiple can be emitted in a single invocation.
    Raises RuntimeError if called outside an agent context.
    """
    try:
        collector = _signal_collector.get()
    except LookupError:
        raise RuntimeError(
            "emit_signal() called outside of an agent context. "
            "Only valid inside a function decorated with @agent(agent_id=...)."
        )
    collector.append(Signal(type=type, reason=reason, metadata=metadata))


async def write_artifact(
    content: bytes,
    content_type: str,
    summary: str,
    confidence: float = 0.0,
    completeness: str = "complete",
) -> ArtifactPointer:
    """
    Write to catalogue and append pointer to AgentResult.artifacts.
    Raises RuntimeError if called outside an agent context.
    """
    try:
        collector = _artifact_collector.get()
    except LookupError:
        raise RuntimeError(
            "write_artifact() called outside of an agent context. "
            "Only valid inside a function decorated with @agent(agent_id=...)."
        )
    pointer = await get_catalogue_writer().write(
        content=content,
        content_type=content_type,
        summary=summary,
        confidence=confidence,
        completeness=completeness,
    )
    collector.append(pointer)
    return pointer
```

### Usage

The functions are callable from the decorated function or any helper it calls. No ctx argument required.

```python
@researcher(command="deep")
async def research_deep(task: str, context: list):
    sources = await fetch_sources(task)

    if len(sources) < 3:
        emit_signal(SignalType.LOW_CONFIDENCE,
                    reason="fewer than 3 sources found",
                    metadata={"count": len(sources)})

    if has_conflicting(sources):
        emit_signal(SignalType.CONFLICTING_SOURCES,
                    reason="sources disagree on key facts")

    emit_progress({"status": "summarising", "source_count": len(sources)})

    report = await summarise(sources)

    if len(report) > 4000:
        pointer = await write_artifact(
            content=report.encode(),
            content_type="text/markdown",
            summary=report[:200],
            confidence=0.85,
        )
        return pointer

    return report
```

### Fatal vs non-fatal

`emit_signal` is for non-fatal observations — the agent continues and returns a result alongside the signals.

Typed exceptions are for fatal conditions — the decorator catches them, execution stops, and they are translated into signals on the failed `AgentResult`.

```python
# non-fatal — agent continues
emit_signal(SignalType.LOW_CONFIDENCE, reason="only 2 sources found")

# fatal — execution stops here
raise NeedsHumanReview(reason="no sources found, cannot proceed")
raise EscalationRequired(reason="content exceeds authority boundary")
```

---

## 3. Signal Vocabulary

### Motivation

Signals are the contract between agents and the orchestration layer. They are typed, versioned, and stable. The vocabulary should be treated the same way the OS treats signal numbers — a fixed set with well-known semantics that all agents and all orchestration code depend on.

Unknown signal types from external agents should raise loudly, not be silently dropped, because an unknown type almost always means a version mismatch between the binary and the SDK.

### Three categories

Signals are grouped by their consequence to the orchestrator, not by their descriptive content.

**Control flow signals** — orchestrator routes on these directly. They change which node executes next.

**Informational signals** — feed into `wave_reflection` QA verdict. They do not change routing directly but influence whether a wave passes or fails.

**Audit signals** — recorded in state and visible to stream consumers. No routing consequence.

```python
class SignalType(StrEnum):

    # Control flow — orchestrator routes on these directly
    NEEDS_HUMAN_REVIEW    = "needs_human_review"     # quality gate, agent uncertain
    ESCALATION_REQUIRED   = "escalation_required"    # authority boundary exceeded
    APPROVAL_REQUIRED     = "approval_required"       # irreversible action pending
    INSUFFICIENT_CONTEXT  = "insufficient_context"   # reroute to researcher
    DEPENDENCY_FAILED     = "dependency_failed"       # upstream result unusable
    RATE_LIMITED          = "rate_limited"            # external API throttling
    TOOL_UNAVAILABLE      = "tool_unavailable"        # external system unreachable

    # Informational — feeds QA reflection verdict, not direct routing
    LOW_CONFIDENCE        = "low_confidence"
    PARTIAL_RESULT        = "partial_result"
    CONFLICTING_SOURCES   = "conflicting_sources"
    REVISION_SUGGESTED    = "revision_suggested"

    # Audit — recorded in state, no routing consequence
    EXTERNAL_ACTION_TAKEN = "external_action_taken"
    CONTENT_OFFLOADED     = "content_offloaded"
    SENSITIVE_CONTENT     = "sensitive_content"


# Groups consumed by collect_wave and routing functions
BLOCKING      = frozenset({
    SignalType.NEEDS_HUMAN_REVIEW,
    SignalType.ESCALATION_REQUIRED,
    SignalType.APPROVAL_REQUIRED,
})
RECOVERABLE   = frozenset({
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
AUDIT         = frozenset({
    SignalType.EXTERNAL_ACTION_TAKEN,
    SignalType.CONTENT_OFFLOADED,
    SignalType.SENSITIVE_CONTENT,
})
ROUTING = BLOCKING | RECOVERABLE
```

### Usage by agent type

```python
# research agent
if len(sources) < 3:
    emit_signal(SignalType.LOW_CONFIDENCE, "fewer than 3 sources", {"count": len(sources)})
if has_conflicting(sources):
    emit_signal(SignalType.CONFLICTING_SOURCES, "sources disagree on key facts")
if has_pii(sources):
    emit_signal(SignalType.SENSITIVE_CONTENT, "PII detected in source material")

# email agent — gate before irreversible action
emit_signal(SignalType.APPROVAL_REQUIRED, "about to send external email",
            {"to": recipient, "subject": subject})
raise EscalationRequired(reason="email requires human approval before sending")

# code / deploy agent — audit trail
result = await deploy(artifact)
emit_signal(SignalType.EXTERNAL_ACTION_TAKEN, "deployed to production",
            {"environment": "prod", "artifact_id": artifact.id})

# analysis agent — partial data
if missing_quarters:
    emit_signal(SignalType.PARTIAL_RESULT, "missing data for some quarters",
                {"missing": missing_quarters})
```

### How collect_wave and routing use signal groups

```python
# graphs/execution.py

async def collect_wave(state: ExecutionState) -> dict[str, Any]:
    current_results = [...]

    def has_any(results, group):
        return any(
            any(s["type"] in group for s in r.get("signals", []))
            for r in results
        )

    return {
        "signals": {
            "has_blocking":          has_any(current_results, BLOCKING),
            "has_recoverable":       has_any(current_results, RECOVERABLE),
            "has_low_confidence":    has_any(current_results, {SignalType.LOW_CONFIDENCE}),
            "has_partial":           has_any(current_results, {SignalType.PARTIAL_RESULT}),
            "has_conflicting":       has_any(current_results, {SignalType.CONFLICTING_SOURCES}),
            "audit":                 [
                s for r in current_results
                for s in r.get("signals", [])
                if s["type"] in AUDIT
            ],
        }
    }


def route_after_reflection(state: ExecutionState) -> str:
    signals = state.get("signals") or {}
    reflections = state.get("wave_reflections") or []
    last = reflections[-1] if reflections else {}
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

## 4. Adapter Layer — Black Box Agents as First Class Citizens

### Motivation

LangGraph nodes are Python async callables. A Rust CLI, an HTTP SSE service, or a gRPC endpoint is not. The adapter layer is the translation boundary — it invokes the external agent, translates its output stream into SDK primitives, and returns an `AgentResult` that LangGraph treats identically to a native Python agent.

From the perspective of `AgentResult` there is no difference between a Python agent calling `emit_signal()` directly and a Rust binary writing `{"type": "signal", ...}` to stdout. The adapter calls `emit_signal()` on the binary's behalf.

### Output event protocol

A minimal typed vocabulary any agent can speak over any output channel.

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
```

### CLI adapter

```python
# sdk/adapters/cli.py

class CLIAdapter:
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
            event_type, data = self.parser.parse(line.decode().rstrip())
            if event_type:
                emit_progress(data) if event_type == "progress" else None

        await proc.wait()
        if proc.returncode != 0:
            stderr = await proc.stderr.read()
            raise RuntimeError(f"CLI exited {proc.returncode}: {stderr.decode()}")

        # materialise artifacts — write_artifact() appends to collector
        for artifact_data in self.parser.pending_artifacts():
            await write_artifact(
                content=artifact_data["content"].encode(),
                content_type=artifact_data.get("content_type", "text/plain"),
                summary=artifact_data.get("summary", ""),
                confidence=artifact_data.get("confidence", 0.0),
            )

        # materialise signals — emit_signal() appends to collector
        for sig_data in self.parser.pending_signals():
            try:
                signal_type = SignalType(sig_data["signal_type"])
            except ValueError:
                raise ValueError(
                    f"Unknown signal type '{sig_data['signal_type']}' from external agent. "
                    f"Valid types: {[s.value for s in SignalType]}"
                )
            emit_signal(
                type=signal_type,
                reason=sig_data.get("reason", ""),
                metadata=sig_data.get("metadata"),
            )

        return self.parser.result()
```

### Rust binary example

The binary just writes JSON lines to stdout. The adapter does the rest.

```rust
// stdout protocol
println!("{}", json!({"type": "progress", "status": "fetching", "done": 3, "total": 10}));
println!("{}", json!({"type": "signal", "signal_type": "low_confidence",
                      "reason": "only 2 sources", "metadata": {"count": 2}}));
println!("{}", json!({"type": "artifact", "content_type": "text/markdown",
                      "summary": "Research on X", "confidence": 0.8,
                      "content": "...full report..."}));
println!("{}", json!({"type": "result", "output": "research complete"}));
```

### Reusable handler factories

```python
# sdk/handlers.py

def langgraph_stream_handler(event_type: str = None):
    """Forwards events to LangGraph custom stream."""
    def handler(data):
        emit_progress({"type": event_type or data.get("type"), **data})
    return handler

def webhook_handler(url: str):
    """POSTs events to a webhook."""
    async def handler(data):
        async with httpx.AsyncClient() as client:
            await client.post(url, json=data)
    return handler

def log_handler(logger, level: str = "info"):
    def handler(data):
        getattr(logger, level)("agent event: %s", data)
    return handler
```

```python
# agents/rust_agent.py

from sdk import Agent
from sdk.adapters.cli import CLIAdapter
from sdk.parsers import JSONLineParser
from sdk.handlers import langgraph_stream_handler, webhook_handler

rust_agent = Agent("rust-researcher")

@rust_agent(command="fast")
async def rust_fast(task: str):
    await CLIAdapter(
        cmd=["./rust_researcher", "--task", task, "--mode", "fast"],
        parser=JSONLineParser(),
    ).run()

rust_agent.use("progress", langgraph_stream_handler())
rust_agent.use("progress", webhook_handler("https://analytics.internal/events"))
```

---

## 5. Public SDK Surface

```python
# Core registration
from sdk import Agent, App

# Context-aware ambient functions
from sdk import emit_progress, emit_signal, write_artifact

# Fatal exceptions — stop execution
from sdk import NeedsHumanReview, EscalationRequired, SemanticError

# Types
from sdk import Signal, SignalType, AgentResult, ArtifactPointer, AgentRunContext

# Signal groups for orchestration routing
from sdk.signals import BLOCKING, RECOVERABLE, INFORMATIONAL, AUDIT, ROUTING

# Adapters — for non-Python agents
from sdk.adapters.cli import CLIAdapter
from sdk.adapters.sse import SSEAdapter

# Parsers
from sdk.parsers import JSONLineParser, PrefixParser

# Reusable handler factories
from sdk.handlers import langgraph_stream_handler, webhook_handler, log_handler
```

---

## 6. File Layout

```
monet/
  app.py                    # compose everything — single source of truth
  graphs.py                 # thin shim for langgraph.json
  langgraph.json

  agents/
    researcher.py           # Agent("researcher"), CLI-backed
    writer.py               # Agent("writer"), Python native
    email_agent.py          # Agent("email-agent"), SSE-backed
    rust_agent.py           # Agent("rust-researcher"), CLI-backed Rust binary

  graphs/
    entry.py                # build_entry_graph()
    planner.py              # build_planner_graph()
    execution.py            # build_execution_graph()

  sdk/
    app.py                  # App, include_agent
    agent.py                # Agent, _build_wrapper, _validate_signature
    _context.py             # contextvars, emit_progress, emit_signal, write_artifact
    signals.py              # SignalType enum, BLOCKING / RECOVERABLE / INFORMATIONAL / AUDIT
    types.py                # AgentRunContext, AgentResult, Signal, ArtifactPointer
    exceptions.py           # NeedsHumanReview, EscalationRequired, SemanticError
    handlers.py             # langgraph_stream_handler, webhook_handler, log_handler
    adapters/
      cli.py                # CLIAdapter
      sse.py                # SSEAdapter
    parsers.py              # OutputParser protocol, JSONLineParser, PrefixParser
```

---

## Design Principles

Registration is explicit. Agents are mounted via `app.include_agent()`, not by importing a file. No hidden side effects.

Context-awareness is structural. `emit_progress`, `emit_signal`, and `write_artifact` resolve their context from `ContextVar` set by the decorator. Each concurrent invocation has its own isolated context. No ctx argument threading required.

Signals are the contract. The signal vocabulary is fixed and versioned. Unknown signal types from external agents raise loudly. The groups `BLOCKING`, `RECOVERABLE`, `INFORMATIONAL`, and `AUDIT` are the only signal semantics the orchestration layer needs to know about.

External agents are first class. A Rust CLI and a Python function are equally valid agent implementations. The adapter layer absorbs the difference. `AgentResult` looks identical from the orchestrator's perspective regardless of how the agent ran.

The catalogue client is pure. `CatalogueClient` has no context awareness — it is a plain async client. Context-awareness lives in `write_artifact()`, the same way `emit_signal()` wraps the signal collector rather than being part of it.
