# monet

A multi-agent orchestration SDK for Python. monet provides a uniform agent interface, lean orchestration state, and automatic content management so you can build multi-agent systems without framework lock-in.

## Key features

- **Uniform agent interface** -- one decorator, one input envelope, one output envelope. Every agent looks the same to the orchestrator regardless of runtime.
- **Lean orchestration state** -- full artifact content never lives in the graph. Summaries and pointers keep state small and inspectable.
- **Automatic content offload** -- large outputs are transparently written to the artifact store. No manual size management.
- **Typed signals** -- agents communicate structured signals (human review needed, escalation, semantic errors) via exceptions. The orchestrator decides what to do with them.
- **Transport-agnostic** -- agents run as local Python functions or over HTTP. Switch between co-located and distributed deployment with configuration, not code changes.
- **Observable by default** -- OpenTelemetry spans at every agent invocation. W3C trace propagation across service boundaries.

## Installation

```bash
pip install monet
```

## Quick example

```python
from monet import agent

@agent(agent_id="researcher")
async def researcher(task: str):
    """Quick lookup for a bounded topic."""
    return await quick_search(task)

@agent(agent_id="researcher", command="deep")
async def researcher_deep(task: str, context: list, effort: str = "high"):
    """Exhaustive research across all available sources."""
    return await deep_research(task, context, effort=effort)
```

## Documentation

- [Getting Started](getting-started.md) -- install, define your first agent, run it
- **Guides**
    - [Defining Agents](guides/agents.md) -- the `@agent` decorator, commands, effort, signals
    - [Artifact Store](guides/artifacts.md) -- storage, metadata, backends
    - [Orchestration](guides/orchestration.md) -- LangGraph nodes, state, HITL
    - [Distribution Mode](guides/distribution.md) -- distributed deployment, CLI, workers, queue providers
    - [Client SDK](guides/client.md) -- MonetClient, event streaming, HITL decisions
    - [Server & Transport](guides/server.md) -- FastAPI server, bootstrap, routes
    - [Observability](guides/observability.md) -- tracing, Langfuse
- **[API Reference](api/core.md)** -- complete reference for all exports
- **Architecture**
    - [Design Principles](architecture/design-principles.md) -- philosophy and influences
    - [Graph Topology](architecture/graph-topology.md) -- three-graph supervisor system (planned)
    - [Roadmap](architecture/roadmap.md) -- what is shipped, in progress, and planned
