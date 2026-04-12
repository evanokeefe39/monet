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

For large outputs, write to the catalogue explicitly:

```python
from monet import agent, write_artifact
from monet.catalogue import InMemoryCatalogueClient, configure_catalogue

# Configure the catalogue backend at startup
configure_catalogue(InMemoryCatalogueClient())

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

If a function returns output longer than 4000 characters and a catalogue client is configured, the decorator automatically offloads the content and returns a pointer. You do not need to call `write_artifact()` for simple cases.

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

async def main():
    client = MonetClient()
    async for event in client.run("Research quantum computing trends"):
        print(type(event).__name__, event)

asyncio.run(main())
```

### In-process (no server)

```python
from monet import run

async def main():
    async for event in run("Research quantum computing trends"):
        print(event)

asyncio.run(main())
```

See [Distribution Mode](guides/distribution.md) for production deployment with workers.

## Next steps

- [Defining Agents](guides/agents.md) -- full guide to the agent SDK
- [Artifact Catalogue](guides/catalogue.md) -- storage, metadata, and backends
- [Orchestration](guides/orchestration.md) -- LangGraph integration
- [Distribution Mode](guides/distribution.md) -- distributed deployment, CLI, workers
- [Client SDK](guides/client.md) -- MonetClient, event streaming, HITL decisions
- [API Reference](api/core.md) -- complete reference for all exports
