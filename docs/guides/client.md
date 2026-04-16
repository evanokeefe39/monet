# Client SDK

`MonetClient` is a graph-agnostic async client for a running monet server. It drives any graph declared in `monet.toml [entrypoints]`, streams typed core events, and exposes generic HITL resume/abort.

Pipeline-specific composition (the default `entry → planning → execution` flow) lives in `monet.pipelines.default` as an *adapter* that consumes core events and yields typed domain events.

## Setup

```python
from monet.client import MonetClient

client = MonetClient(url="http://localhost:2026")
```

The default URL points to an Aegra server started with `monet dev`.

## Running the default pipeline

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

With `auto_approve=True`, plan approval interrupts resolve automatically. Execution interrupts always pause.

## Running a single graph

```python
async for event in client.run("my-graph", {"task": "...", "run_id": "abc"}):
    print(event)
```

`MonetClient.run()` drives any declared entrypoint and yields *core* events only. Pass either a state dict as input, or a topic string which is wrapped by `task_input()`.

## Event types

### Core events (from `monet.client`)

All events are frozen dataclasses carrying a `run_id`.

| Event | Source | Fields |
|---|---|---|
| `RunStarted` | thread + run creation | `graph_id`, `thread_id` |
| `NodeUpdate` | langgraph `updates` stream | `node`, `update` |
| `AgentProgress` | `emit_progress` custom writer | `agent_id`, `status`, `reasons` |
| `SignalEmitted` | `emit_signal` custom writer | `agent_id`, `signal_type`, `payload` |
| `Interrupt` | langgraph `interrupt()` | `tag`, `values`, `next_nodes` |
| `RunComplete` | run terminated OK | `final_values` |
| `RunFailed` | run errored | `error` |

### Default-pipeline events (from `monet.pipelines.default`)

Yielded by `run_default(...)` in addition to `RunComplete` / `RunFailed`.

| Event | Fields |
|---|---|
| `TriageComplete` | `complexity`, `suggested_agents` |
| `PlanReady` | `goal`, `nodes` |
| `PlanApproved` | — |
| `PlanInterrupt` | `work_brief_pointer`, `routing_skeleton` |
| `WaveComplete` | `wave_index`, `node_ids`, `results` |
| `ReflectionComplete` | `verdict`, `notes` |
| `ExecutionInterrupt` | `reason`, `last_result`, `pending_node_ids` |

## HITL decisions

### Default pipeline (typed verbs)

The default pipeline's HITL verbs wrap `client.resume(...)` with typed tags and TypedDict payloads:

```python
from monet.pipelines.default import (
    abort_run,
    approve_plan,
    reject_plan,
    retry_wave,
    revise_plan,
    run as run_default,
)
from monet.pipelines.default import ExecutionInterrupt, PlanInterrupt

async for event in run_default(client, "Analyze market trends"):
    if isinstance(event, PlanInterrupt):
        await approve_plan(client, event.run_id)
        # Or: await revise_plan(client, event.run_id, "Add competitive analysis")
        # Or: await reject_plan(client, event.run_id)
        break

    if isinstance(event, ExecutionInterrupt):
        await retry_wave(client, event.run_id)
        # Or: await abort_run(client, event.run_id)
        break
```

### Generic resume (any graph)

```python
from monet.client import Interrupt

async for event in client.run("my-graph", input=...):
    if isinstance(event, Interrupt):
        await client.resume(event.run_id, event.tag, payload={"approved": True})
        break
```

`resume()` validates that the thread is actually paused at `tag` before dispatching. It raises:

| Exception | When |
|---|---|
| `RunNotInterrupted` | No interrupted thread found for `run_id` |
| `AlreadyResolved` | Run already moved past the interrupt (retry guard) |
| `AmbiguousInterrupt` | Multiple pending nodes — caller must disambiguate |
| `InterruptTagMismatch` | `tag` does not match the current interrupt node |
| `GraphNotInvocable` | Graph ID not declared in `[entrypoints]` |

All inherit from `MonetClientError`.

## Querying runs

### List recent runs

```python
runs = await client.list_runs(limit=10)
for run in runs:
    print(f"{run.run_id}: {run.status} ({run.completed_stages})")
```

Returns `list[RunSummary]` with `run_id`, `status`, `completed_stages`, and `created_at`.

### Get run details

```python
detail = await client.get_run(run_id)
print(detail.status)
print(detail.completed_stages)
print(detail.values)            # merged state from all threads
print(detail.pending_interrupt) # Interrupt | None
```

`RunDetail` is a generic view over any run. For default-pipeline runs, the typed projection gives you typed fields:

```python
from monet.pipelines.default import DefaultPipelineRunDetail

view = DefaultPipelineRunDetail.from_run_detail(detail)
print(view.routing_skeleton, view.wave_results, view.wave_reflections)
```

### List pending decisions

```python
pending = await client.list_pending()
for p in pending:
    print(f"{p.run_id}: {p.decision_type}")
```

`decision_type` is the raw interrupt tag (the graph node name that called `interrupt()`). Pipeline adapters supply friendlier summaries.

## Entrypoints

Only graphs declared in `monet.toml [entrypoints.<name>]` can be driven via `MonetClient.run()`. Internal subgraphs of the default pipeline (`planning`, `execution`) are intentionally not declared.

```toml
[entrypoints.default]
graph = "entry"
```

Custom graphs ship with their own entrypoint:

```toml
[entrypoints.review]
graph = "review"
```

Attempting `client.run("planning", ...)` raises `GraphNotInvocable`.

## In-process driver

The in-process `MemorySaver` driver has been removed. Use `monet dev` to start a local server and drive through `MonetClient`, or invoke `aegra dev` directly.
