# Defining Agents

The agent SDK is the core of monet. It provides the `@agent` decorator and supporting utilities that turn any Python callable into an agent with a uniform interface.

## The `@agent` decorator

The decorator wraps a function as an agent handler, registering it by `agent_id` and `command`:

```python
from monet import agent

@agent(agent_id="researcher", command="deep")
async def researcher_deep(task: str, context: list, effort: str = "high"):
    """
    Exhaustive research across all available sources for a given topic.
    Produces catalogue artifacts with findings and a confidence-weighted
    synthesis.
    """
    return await deep_research(task, context, effort=effort)
```

Parameters:

- `agent_id` (str, required) -- unique identifier for the agent
- `command` (str, optional) -- command name, defaults to `"fast"`

The decorator captures the function's docstring at decoration time and stores it alongside the registration. This description is available to planners reasoning about which agent and command to invoke. A missing docstring emits a warning.

### Parameter injection

The decorator inspects the function signature at decoration time. Each parameter must match a field on `AgentRunContext`. At call time, matching fields are injected by name. Declare only what you need:

```python
# Minimal -- only needs the task
@agent(agent_id="researcher")
async def researcher(task: str):
    """Quick lookup for a bounded topic."""
    return await quick_search(task)

# Full context access
@agent(agent_id="analyst", command="deep-analysis")
async def analyst_deep(task: str, context: list, effort: str):
    """Multi-step analysis across structured and unstructured data."""
    return await multi_step_analysis(task, context, effort=effort)
```

Available fields for injection:

| Field | Type | Description |
|---|---|---|
| `task` | `str` | Natural language instruction |
| `context` | `list` | Typed context entry list |
| `command` | `str` | Registered command name (e.g. `"fast"`, `"deep"`) |
| `effort` | `str \| None` | `"low"`, `"medium"`, or `"high"`. `None` if not passed |
| `trace_id` | `str` | OpenTelemetry trace ID |
| `run_id` | `str` | LangGraph run ID |
| `agent_id` | `str` | The agent's registered ID |
| `skills` | `list[str]` | Skill identifiers loaded for this invocation |

A parameter name that does not match any `AgentRunContext` field raises `TypeError` at decoration time, not at call time.

## Commands

Commands are plain strings. Two conventional names carry implied calling conventions:

- `"fast"` -- synchronous, bounded effort, returns an inline result. The default when no command is specified.
- `"deep"` -- async, long-running, writes catalogue artifacts and returns pointers.

Domain-specific commands have no implied convention:

```python
@agent(agent_id="writer", command="translate")
async def writer_translate(task: str, context: list, effort: str):
    """Translate content into a target language specified in the task."""
    return await translate(task, context, effort=effort)

@agent(agent_id="analyst", command="ask")
async def analyst_ask(task: str):
    """Ad hoc query against available data sources."""
    return await ad_hoc_query(task)
```

The same `agent_id` with different `command` values registers distinct capabilities of the same agent.

## Effort

Effort is passed by the orchestrator at invocation time. It tells the agent how much work to do for this particular call.

```python
@agent(agent_id="planner", command="plan")
async def planner(task: str, context: list, effort: str = "high"):
    if effort == "low":
        return await quick_replan(task, context)
    elif effort == "medium":
        return await focused_plan(task, context)
    return await full_plan(task, context)
```

Three values: `"low"`, `"medium"`, `"high"`. If absent, the agent uses its own default. The orchestrator does not control which model an agent uses internally -- model selection is an internal agent concern.

## Automatic content offload

When a function returns a value exceeding 4000 characters and a catalogue client is configured, the decorator writes the full content to the catalogue and returns a pointer. This is transparent -- no explicit `write_artifact()` call needed for simple cases.

For explicit control over artifacts (multiple named artifacts, custom metadata):

```python
from monet import agent, write_artifact

@agent(agent_id="researcher", command="deep")
async def researcher(task: str, context: list):
    """Deep research producing multiple artefacts."""
    findings = await search_sources(task)
    synthesis = await synthesise(findings)

    write_artifact(
        content=findings.encode(),
        content_type="application/json",
        summary="Raw research findings",
        confidence=0.9,
        completeness="complete",
    )

    return synthesis
```

## Typed exceptions

Agents communicate structured signals by raising typed exceptions. The decorator catches them and populates `AgentResult.signals`. Partial artifacts written before the exception are preserved.

### `NeedsHumanReview`

The agent has partial output but needs human judgment to proceed:

```python
raise NeedsHumanReview(reason="Conflicting sources, cannot resolve automatically")
```

### `EscalationRequired`

The agent has hit a capability or permissions boundary:

```python
raise EscalationRequired(reason="Requires API key for premium data source")
```

### `SemanticError`

Soft failure -- no results, quality below threshold, or irreconcilable conflict:

```python
raise SemanticError(type="no_results", message="No sources found for this topic")
```

Unexpected exceptions are caught and wrapped as `SemanticError(type="unexpected_error")`.

## `AgentResult`

The decorator always returns an `AgentResult`. Never constructed manually.

| Field | Type | Description |
|---|---|---|
| `success` | `bool` | Did the agent complete without a semantic error |
| `output` | `str \| ArtifactPointer` | Inline result or catalogue pointer |
| `confidence` | `float` | Agent-declared confidence (0.0--1.0) |
| `artifacts` | `list[ArtifactPointer]` | Collected from `write_artifact()` calls |
| `signals` | `AgentSignals` | Populated from typed exceptions |
| `trace_id` | `str` | Echoed from input |
| `run_id` | `str` | Echoed from input |

## `AgentRunContext`

Available anywhere inside a decorated function via `get_run_context()`:

```python
from monet import get_run_context

@agent(agent_id="myagent")
async def my_agent(task: str):
    ctx = get_run_context()
    print(f"Running as {ctx.agent_id} with command {ctx.command}")
    return "done"
```

Outside the decorator, `get_run_context()` returns a safe default so functions remain testable without orchestration infrastructure.

## Context entries

The `context` field on `AgentRunContext` is a list of typed entries using a Pydantic discriminated union on the `type` field:

| Type | Class | Use |
|---|---|---|
| `"artifact"` | `ArtifactEntry` | Pointer to a catalogue artifact |
| `"work_brief"` | `WorkBriefEntry` | Structured work brief |
| `"constraint"` | `ConstraintEntry` | Constraint on the task |
| `"instruction"` | `InstructionEntry` | Direct instruction (e.g. revision notes) |
| `"skill_reference"` | `SkillReferenceEntry` | Reference to a skill file |

Each entry has: `type`, `summary`, `url`, `content`, `content_type`. The URL points to a catalogue artifact. The agent decides from the summary whether to fetch the full content.

## Input envelope

The HTTP contract for invoking an agent:

| Field | Type | Required | Description |
|---|---|---|---|
| `task` | string | yes | Natural language instruction |
| `context` | list | no | Typed context entries |
| `command` | string | no | Command name (default `"fast"`) |
| `effort` | enum | no | `"low"`, `"medium"`, or `"high"` |
| `trace_id` | string | yes | OTel trace ID |
| `run_id` | string | yes | LangGraph run ID |

## Output envelope

| Field | Type | Description |
|---|---|---|
| `success` | bool | Completion status |
| `output` | string or pointer | Result content |
| `confidence` | float | 0.0--1.0 |
| `artifacts` | list | Artifact pointers |
| `signals` | object | Structured signals |
| `trace_id` | string | Echoed |
| `run_id` | string | Echoed |

## Signals

Present on every output envelope. The orchestrator reads these and acts according to its own policy.

| Field | Type | Description |
|---|---|---|
| `needs_human_review` | bool | Orchestrator calls `interrupt()` |
| `review_reason` | string or null | What the human needs to assess |
| `escalation_requested` | bool | Capability or permissions boundary hit |
| `escalation_reason` | string or null | What is needed |
| `revision_notes` | object or null | Feedback for replanning |
| `semantic_error` | object or null | Soft failure info (`type` + `message`) |

## Streaming progress

Agents can optionally emit intra-node progress events via `emit_progress()`. LangGraph nodes emit start and completion events automatically. Anything richer is opt-in and agent-specific.

### Python SDK agents

Call `emit_progress()` at whatever granularity makes sense:

```python
from monet import agent, emit_progress

@agent(agent_id="researcher", command="deep")
async def researcher(task: str, context: list, effort: str = "high"):
    """Deep research with progress updates."""
    sources = await gather_sources(task, depth=effort)
    results = []
    for i, source in enumerate(sources):
        result = await process(source)
        results.append(result)
        emit_progress({"searched": i + 1, "total": len(sources)})
    return synthesise(results)
```

`emit_progress()` is currently a no-op. It will be wired to LangGraph's `get_stream_writer()` when the orchestration graph is built. Functions remain testable without orchestration infrastructure.

### CLI agents -- stdout streaming

A CLI compiled from any language writes progress events as newline-delimited JSON to stdout. A Python wrapper spawns the subprocess, reads stdout in a loop, and forwards progress events via `emit_progress()`. The CLI itself has no SDK dependency -- it writes to stdout, which is its natural output channel.

```python
import asyncio
import json

from monet import agent, emit_progress

@agent(agent_id="rust-analyst", command="deep-analysis")
async def rust_analyst(task: str, run_id: str, effort: str = "high"):
    """Deep analysis delegated to a Rust CLI binary."""
    proc = await asyncio.create_subprocess_exec(
        "./analyst", "--task", task, "--run-id", run_id, "--effort", effort,
        stdout=asyncio.subprocess.PIPE,
    )
    result_line = None
    async for line in proc.stdout:
        event = json.loads(line)
        if event["type"] == "progress":
            emit_progress(event)
        elif event["type"] == "result":
            result_line = event
    await proc.wait()
    return result_line["output"]
```

The CLI developer chooses whether to emit progress at all. The wrapper developer chooses whether to forward it. Both decisions are independent.

### HTTP agents -- async results with pull streaming

For HTTP agents doing long-running work, holding an HTTP connection open for many minutes is fragile. An agent can implement an async response pattern -- return 202 Accepted with a task ID immediately, then expose its own status and stream endpoints:

```python
@agent(agent_id="deep-researcher", command="deep")
async def deep_researcher(task: str, run_id: str, trace_id: str, effort: str = "high"):
    """Long-running research via an external HTTP service."""
    response = await http_client.post(
        "http://researcher-service/deep",
        headers={"traceparent": trace_id},
        json={"task": task, "run_id": run_id, "effort": effort},
    )

    if response.status_code == 202:
        task_id = response.json()["task_id"]
        stream_url = response.json().get("stream_url")

        if stream_url:
            # SSE stream -- subscribe and forward progress
            async with http_client.stream("GET", stream_url) as stream:
                async for line in stream.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    if event["type"] == "progress":
                        emit_progress(event)
                    elif event["type"] == "result":
                        return event["output"]
        else:
            # Polling fallback
            while True:
                status = await http_client.get(
                    f"http://researcher-service/status/{task_id}"
                )
                data = status.json()
                if data.get("progress"):
                    emit_progress(data["progress"])
                if data["status"] == "complete":
                    return data["output"]
                await asyncio.sleep(5)
    else:
        # Synchronous -- result is in the response body
        return response.json()["output"]
```

The HTTP agent service hosts its own `/status` and `/stream` endpoints. Agents that do not implement them simply return 200 with the result when done -- the node wrapper handles both cases.

## Non-Python agents

Any service that implements the HTTP input/output envelope contract can participate as an agent. A Python wrapper function delegates to the external service:

```python
@agent(agent_id="rust-analyst")
async def rust_analyst(task: str):
    """Fast bounded analysis via the Rust analytics service."""
    response = await http_client.post(
        "http://rust-service/run",
        json={"task": task},
    )
    return response.json()["result"]
```

The orchestrator has no knowledge of the agent's internal runtime. The dependency direction is always clean -- the orchestrator reaches into the agent when it wants information. The agent never makes outbound calls to the orchestrator.
