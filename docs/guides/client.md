# Client SDK

The `MonetClient` provides a typed async interface for interacting with a running monet server. It handles run lifecycle, event streaming, and HITL (Human-In-The-Loop) decisions.

## Setup

```python
from monet.client import MonetClient

client = MonetClient(url="http://localhost:2024")
```

The default URL points to a LangGraph dev server started with `monet dev`. For a production orchestration server, use its URL instead.

## Starting a run

```python
import asyncio
from monet.client import MonetClient

async def main():
    client = MonetClient()

    async for event in client.run("Research quantum computing trends"):
        print(type(event).__name__, event)

asyncio.run(main())
```

`run()` returns an async iterator of typed events. The run progresses through triage, planning, and execution, yielding events at each stage.

### Auto-approval

```python
async for event in client.run("Write a blog post", auto_approve=True):
    print(event)
```

With `auto_approve=True`, planning interrupts are automatically approved. Execution interrupts always pause regardless of this setting.

## Event types

Events are frozen dataclasses. The full set:

| Event | Stage | Fields | Description |
|---|---|---|---|
| `TriageComplete` | Entry | `complexity`, `suggested_agents` | Triage classified the request |
| `PlanReady` | Planning | `goal`, `phases`, `assumptions` | Work brief generated |
| `PlanApproved` | Planning | — | Plan was approved |
| `PlanInterrupt` | Planning | `brief` | Awaiting human decision |
| `AgentProgress` | Execution | `agent_id`, `status` | Real-time agent progress |
| `WaveComplete` | Execution | `phase_index`, `wave_index`, `results` | Execution wave finished |
| `ReflectionComplete` | Execution | `verdict`, `notes` | QA reflection result |
| `ExecutionInterrupt` | Execution | `reason`, `phase_index`, `wave_index` | Execution paused |
| `RunComplete` | Done | `wave_results`, `wave_reflections` | Run finished successfully |
| `RunFailed` | Done | `error` | Run failed |

All events carry a `run_id` field.

## HITL decisions

When the run yields a `PlanInterrupt`, three actions are available:

```python
async for event in client.run("Analyze market trends"):
    if isinstance(event, PlanInterrupt):
        # Option 1: approve
        async for e in client.approve_plan(event.run_id):
            print(e)

        # Option 2: revise
        async for e in client.revise_plan(event.run_id, "Add competitive analysis"):
            print(e)

        # Option 3: reject
        await client.reject_plan(event.run_id)
```

When the run yields an `ExecutionInterrupt`:

```python
    if isinstance(event, ExecutionInterrupt):
        # Retry the current wave
        async for e in client.retry_wave(event.run_id):
            print(e)

        # Or abort the run
        await client.abort_run(event.run_id)
```

## Querying runs

### List recent runs

```python
runs = await client.list_runs(limit=10)
for run in runs:
    print(f"{run.run_id}: {run.status} ({run.phase})")
```

Returns a list of `RunSummary` with `run_id`, `status`, `phase`, and `created_at`.

### Get run details

```python
detail = await client.get_run(run_id)
print(detail.triage)
print(detail.work_brief)
print(detail.wave_results)
```

Returns a `RunDetail` with full state: triage output, work brief, wave results, and wave reflections.

### Get artifacts

```python
artifacts = await client.get_artifacts(run_id)
for a in artifacts:
    print(a["artifact_id"], a["url"])
```

Collects all artifact pointers from a run's wave results.

### List pending decisions

```python
pending = await client.list_pending()
for p in pending:
    print(f"{p.run_id}: {p.decision_type} - {p.summary}")
```

Returns runs waiting for human input. Each `PendingDecision` has `run_id`, `decision_type` (`"plan_approval"` or `"execution_review"`), `summary`, and `detail`.

## In-process alternative

For local development or testing without a server, use the in-process `run()` function:

```python
from monet import run

async def main():
    async for event in run("Research quantum computing"):
        print(event)
```

This runs the full pipeline locally with auto-approved plans. It yields the same `RunEvent` types as `MonetClient`. No server setup required.

```python
async for event in run("My topic", run_id="custom-id", enable_tracing=True):
    ...
```

| Parameter | Default | Description |
|---|---|---|
| `topic` | required | User request |
| `run_id` | auto-generated | Run identifier |
| `enable_tracing` | `False` | Configure OpenTelemetry tracing |
