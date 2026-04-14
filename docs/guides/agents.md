# Defining Agents

The agent SDK is the core of monet. It provides the `@agent` decorator, the ambient functions (`emit_progress`, `emit_signal`, `write_artifact`), and the `AgentStream` event bus for integrating external agents.

## The `@agent` decorator

Two equivalent call forms — both register at decoration time (import time).

```python
from monet import agent

# Form 1 — bound partial (recommended for multi-command agents)
researcher = agent("researcher")

@researcher(command="fast")
async def researcher_fast(task: str) -> str:
    """Quick lookup for a bounded topic."""
    return await quick_search(task)

@researcher(command="deep")
async def researcher_deep(task: str, context: list) -> str:
    """Exhaustive research producing artifact store artifacts."""
    return await deep_research(task, context)
```

```python
# Form 2 — verbose (with pool assignment)
@agent(agent_id="writer", command="deep", pool="default")
async def writer_deep(task: str) -> str:
    """Generate the article body from the brief."""
    return await write(task)
```

The decorator has two jobs only: registration and context injection. It does not detect transports, branch on return type, or know about `AgentStream`. Before calling the function it sets `contextvars` so the ambient functions resolve correctly anywhere in the call stack — including inside `AgentStream.run()`.

### Parameter injection

Each parameter on the decorated function must match a field on `AgentRunContext`. At call time, matching fields are injected by name. Declare only what you need:

| Field | Type | Description |
|---|---|---|
| `task` | `str` | Natural language instruction |
| `context` | `list[dict]` | Typed context entry list |
| `command` | `str` | Registered command name |
| `trace_id` | `str` | OpenTelemetry trace ID |
| `run_id` | `str` | LangGraph run ID |
| `agent_id` | `str` | The agent's registered ID |
| `skills` | `list[str]` | Skill identifiers loaded for this invocation |

A parameter name not in this set raises `TypeError` at decoration time.

## Commands

Commands are plain strings. Two conventional names carry implied calling conventions:

- `"fast"` — bounded effort, returns an inline result. Default when no command is specified.
- `"deep"` — long-running, typically writes artifact store artifacts.

Domain-specific commands have no implied convention. The same `agent_id` with different `command` values registers distinct capabilities of the same agent.

## Returning results

`@agent` functions can return:

- **A string** — becomes `AgentResult.output`. If it exceeds `DEFAULT_CONTENT_LIMIT` (4000 bytes) and a artifact store backend is configured, the full content is automatically offloaded as an artifact and `output` becomes a 200-character inline summary.
- **A dict** — becomes `AgentResult.output` directly (e.g. structured planner output, triage decisions).
- **`None`** — when the primary output is one or more artifacts already written via `write_artifact()`.

For multiple named artifacts or custom metadata, call `write_artifact` explicitly:

```python
from monet import agent, write_artifact

researcher = agent("researcher")

@researcher(command="deep")
async def researcher_deep(task: str) -> None:
    findings = await search_sources(task)
    synthesis = await synthesise(findings)

    await write_artifact(
        content=findings.encode(),
        content_type="application/json",
        summary="Raw research findings",
        confidence=0.9,
    )
    await write_artifact(
        content=synthesis.encode(),
        content_type="text/markdown",
        summary=synthesis[:200],
        confidence=0.85,
    )
```

## Agent quality responsibility

The orchestrator treats agents as potentially untrusted black boxes of unknown quality. It provides mechanisms for agents to communicate failure and quality concerns, but it cannot enforce output quality. If your research agent hallucinates, monet cannot fix that design flaw. If your writer agent produces thin content, the framework will not thicken it.

Three lines of defense exist, and each agent author should understand them:

1. **Agent self-validation (your job).** Validate your own output before returning it. If a required tool fails, raise `EscalationRequired` to halt execution immediately. If output quality is uncertain, emit a `LOW_CONFIDENCE` signal. Don't return garbage and hope the next layer catches it.
2. **QA reflection gates.** The execution graph runs QA agents after each wave. QA evaluates semantic quality: is the content well-cited, does it address the brief, is it complete? QA catches content defects that structural checks cannot.
3. **Human review gates.** BLOCKING signals pause execution for human decision. The human can abort a run that no automated check caught.

An agent that catches all exceptions and returns empty or garbage content will pass the decorator's structural checks (non-empty string). It will eventually be caught by QA or human review, but it wastes time and compute. Good citizenship means raising the right signal at the right time.

### Resolving upstream content

Downstream agents receive upstream output as short summaries plus artifact store pointers. To access the full content, call `resolve_context()`:

```python
from monet import agent, resolve_context

writer = agent("writer")

@writer(command="deep")
async def writer_deep(task: str, context: list) -> str:
    # Without this call, context entries contain only 200-char summaries.
    context = await resolve_context(context)
    # Now each entry has a 'content' field with the full upstream text.
    return await synthesise(task, context)
```

Every agent that consumes upstream output (writer, QA, publisher) should call `resolve_context()` before using the context. See [Pointer-only state](orchestration.md#pointer-only-state) for the design rationale.

### Signal strategy

| Situation | What to do | Signal group | Result |
|---|---|---|---|
| Required tool is broken | `raise EscalationRequired(reason)` | BLOCKING | Immediate interrupt |
| Content needs human review | `raise NeedsHumanReview(reason)` | BLOCKING | Immediate interrupt |
| Retryable error | `raise SemanticError(type, message)` | RECOVERABLE | Automatic retry |
| Quality concern (non-fatal) | `emit_signal(Signal(type=LOW_CONFIDENCE, ...))` | INFORMATIONAL | Passed to QA |
| Partial result | `emit_signal(Signal(type=PARTIAL_RESULT, ...))` | INFORMATIONAL | Passed to QA |

The orchestrator routes on signal *groups*, never individual types. Your agent decides the severity; the orchestrator enforces the consequence.

## Signals — non-fatal events

Use `emit_signal` to surface non-fatal events. Signals accumulate; the agent continues. The orchestrator routes on signal *groups*, never raw strings — see [`docs/api/core.md`](../api/core.md#signaltype-and-routing-groups).

```python
from monet import agent, emit_signal, Signal, SignalType

researcher = agent("researcher")

@researcher(command="fast")
async def researcher_fast(task: str) -> str:
    sources = await fetch_sources(task)
    if len(sources) < 3:
        emit_signal(Signal(
            type=SignalType.LOW_CONFIDENCE,
            reason="fewer than 3 sources",
            metadata={"count": len(sources)},
        ))
    return await synthesise(sources)
```

## Typed exceptions — fatal conditions

When the agent cannot usefully continue, raise a typed exception. The decorator catches it and translates it into a `Signal` on `AgentResult`. Partial artifacts already written are preserved.

```python
from monet import NeedsHumanReview, EscalationRequired, SemanticError

raise NeedsHumanReview(reason="Conflicting sources, cannot resolve automatically")
raise EscalationRequired(reason="Requires API key for premium data source")
raise SemanticError(type="no_results", message="No sources found for this topic")
```

Unexpected exceptions are wrapped as `SemanticError(type="unexpected_error")`.

## Progress — `emit_progress`

Call `emit_progress` at whatever granularity makes sense. It writes to the LangGraph stream writer, so callers subscribing via `astream_events` see all progress events from all agents without additional wiring. Outside a LangGraph context it is a no-op, so functions remain testable without orchestration infrastructure.

```python
from monet import emit_progress

@researcher(command="deep")
async def researcher_deep(task: str) -> str:
    sources = await gather_sources(task)
    for i, source in enumerate(sources):
        await process(source)
        emit_progress({"searched": i + 1, "total": len(sources)})
    return "done"
```

## Integrating external agents — `AgentStream`

`AgentStream` is the translation boundary between an external agent (subprocess, HTTP service, SSE stream) and the SDK primitives. It reads typed JSON events, fires registered handlers, and applies sensible defaults for the rest.

The minimal integration is four lines:

```python
from monet import agent, AgentStream

researcher = agent("researcher")

@researcher(command="fast")
async def researcher_fast(task: str) -> None:
    await AgentStream.cli(cmd=["./researcher", "--task", task]).run()
```

Defaults handle everything: artifacts go to the artifact store, signals reach the collector, progress flows to the LangGraph stream, errors raise `SemanticError`. Register `.on()` handlers only for non-default behaviour:

```python
from monet import AgentStream, write_artifact, webhook_handler, log_handler
import logging

logger = logging.getLogger(__name__)

@researcher(command="deep")
async def researcher_deep(task: str) -> None:
    await (
        AgentStream.cli(cmd=["./researcher", "--task", task, "--mode", "deep"])
        .on("artifact", write_artifact)
        .on("artifact", webhook_handler("https://renderer.internal/artifacts"))
        .on("error", log_handler(logger, level="error"))
        .run()
    )
```

The contextvars set by `@agent` live for the entire body of the function, so `write_artifact`, `emit_signal`, and `emit_progress` all resolve correctly inside `.run()`.

### Event protocol

The external binary writes newline-delimited JSON to stdout (CLI) or SSE data fields (HTTP). Five event types are defined; unknown `signal_type` values raise `ValueError` before any handler fires — version mismatches between the binary and the SDK are loud failures.

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

### Standalone CLI example

The external agent has no monet dependency — it just writes JSON to stdout.

```python
# researcher_cli.py
import json, click

@click.command()
@click.option("--task", required=True)
def main(task: str) -> None:
    print(json.dumps({"type": "progress", "status": "starting", "done": 0, "total": 1}), flush=True)
    report = synthesise(task)
    print(json.dumps({
        "type": "artifact",
        "content_type": "text/markdown",
        "summary": report[:200],
        "confidence": 0.85,
        "completeness": "complete",
        "content": report,
    }), flush=True)
    print(json.dumps({"type": "result", "output": "done"}), flush=True)

if __name__ == "__main__":
    main()
```

The monet wrapper invokes it via `AgentStream.cli(...)` and lets defaults handle the rest.

### Other transports

| Constructor | Use |
|---|---|
| `AgentStream.cli(cmd=[...])` | subprocess stdout |
| `AgentStream.sse(url=...)` | HTTP Server-Sent Events |
| `AgentStream.http(url=..., interval=...)` | HTTP polling — stops on the first `result` event |
| `AgentStream.grpc(...)` | reserved — subclass `AgentStream` and override `_iter_events()` |

## `get_run_context` and `get_run_logger`

Available anywhere inside a decorated function:

```python
from monet import get_run_context, get_run_logger

@researcher(command="fast")
async def researcher_fast(task: str) -> str:
    ctx = get_run_context()
    log = get_run_logger()
    log.info("running %s/%s", ctx["agent_id"], ctx["command"])
    return "done"
```

Outside a decorated function, both return safe defaults so functions remain testable without orchestration infrastructure.
