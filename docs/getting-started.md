# Getting Started

## Installation

```bash
pip install monet
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add monet
```

## Define an agent

An agent is any Python function decorated with `@agent`. The decorator requires an `agent_id` and optionally a `command` name (defaults to `"fast"`).

```python
from monet import agent

@agent(agent_id="greeter")
def greet(task: str) -> str:
    """Respond to a greeting."""
    return f"Hello! You said: {task}"
```

The function's parameters are injected from the runtime context by name. Declare only the fields you need -- any `AgentRunContext` field works: `task`, `context`, `command`, `effort`, `trace_id`, `run_id`, `agent_id`, `skills`.

## Async agents

Async functions work the same way:

```python
@agent(agent_id="researcher", command="deep")
async def research(task: str, context: list, effort: str = "high") -> str:
    """Deep research across multiple sources."""
    results = await gather_sources(task, depth=effort)
    return synthesise(results)
```

## Effort levels

The orchestrator passes an `effort` level (`"low"`, `"medium"`, `"high"`) to control how much work an agent does per invocation. Declare it as a parameter to receive it:

```python
@agent(agent_id="planner", command="plan")
async def plan(task: str, context: list, effort: str = "high") -> str:
    """Create a structured work plan."""
    if effort == "low":
        return await quick_replan(task, context)
    return await full_plan(task, context)
```

## Typed exceptions for signals

Agents communicate structured signals to the orchestrator by raising typed exceptions. The decorator catches them and translates them into `AgentResult.signals`.

```python
from monet import agent, NeedsHumanReview, EscalationRequired, SemanticError

@agent(agent_id="publisher", command="publish")
async def publish(task: str, context: list) -> str:
    """Publish content to the target platform."""
    draft = await prepare_publication(task, context)

    if not draft.meets_quality_bar():
        raise NeedsHumanReview(reason="Draft quality below threshold")

    if not has_publish_permissions():
        raise EscalationRequired(reason="Missing publish credentials")

    if not draft.has_content():
        raise SemanticError(type="no_content", message="Nothing to publish")

    return await execute_publish(draft)
```

| Exception | Signal type | When to use |
|---|---|---|
| `NeedsHumanReview(reason)` | `NEEDS_HUMAN_REVIEW` (BLOCKING) | Partial output exists but needs human judgment |
| `EscalationRequired(reason)` | `ESCALATION_REQUIRED` (BLOCKING) | Agent hit a capability or permissions boundary |
| `SemanticError(type, message)` | `SEMANTIC_ERROR` (RECOVERABLE) | Soft failure -- no results, quality too low, irreconcilable conflict |

Unexpected exceptions are caught and wrapped as `SemanticError(type="unexpected_error")`. Infrastructure exceptions never crash the LangGraph node.

## Writing artifacts

For large outputs, write to the artifact store explicitly:

```python
from monet import agent, write_artifact
from monet.artifacts import InMemoryArtifactClient, configure_artifacts

# Configure the artifact store backend at startup
configure_artifacts(InMemoryArtifactClient())

writer = agent("writer")

@writer(command="deep")
async def write_report(task: str, context: list) -> str:
    """Produce a long-form report."""
    report = await generate_report(task, context)

    pointer = await write_artifact(
        content=report.encode(),
        content_type="text/markdown",
        summary="Market analysis report",
        confidence=0.85,
    )
    return f"Report written: {pointer['artifact_id']}"
```

If a function returns output longer than 4000 characters and a artifact store client is configured, the decorator automatically offloads the content and returns a pointer. You do not need to call `write_artifact()` for simple cases.

## Running a pipeline

monet includes a CLI and client SDK for running topics through the full orchestration pipeline.

### Development server

```bash
monet dev --port 2026
```

### Submit work via CLI

```bash
monet run "Research quantum computing trends"
```

### Submit work via Python

```python
import asyncio
from monet.client import MonetClient
from monet.pipelines.default import run as run_default

async def main():
    client = MonetClient()
    async for event in run_default(client, "Research quantum computing trends"):
        print(type(event).__name__, event)

asyncio.run(main())
```

`MonetClient` drives any graph declared in `monet.toml [entrypoints]`. The default pipeline (`entry → planning → execution` with HITL plan approval) ships as an adapter in `monet.pipelines.default`. Single-graph invocations use `client.run(graph_id, input)` directly — see the [Client guide](guides/client.md).

> **No in-process driver.** The `from monet import run` path has been removed. Use `monet dev` to start a local server, or invoke `aegra dev` directly.

See [Distribution Mode](guides/distribution.md) for production deployment with workers.

## Next steps

- [Defining Agents](guides/agents.md) -- full guide to the agent SDK
- [Artifact Store](guides/artifacts.md) -- storage, metadata, and backends
- [Orchestration](guides/orchestration.md) -- LangGraph integration
- [Distribution Mode](guides/distribution.md) -- distributed deployment, CLI, workers
- [Client SDK](guides/client.md) -- MonetClient, event streaming, HITL decisions
- [API Reference](api/core.md) -- complete reference for all exports
