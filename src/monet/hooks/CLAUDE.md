# monet.hooks — Built-in Worker Hooks

## Responsibility

Shipped worker-side hooks that run in the worker process via `@on_hook`.

## Directory structure

```
hooks/
  __init__.py       Imports prebuilt/ for side-effect registration
  prebuilt/         monet's shipped hook implementations
    plan_context.py inject_plan_context — resolves work_brief_pointer on worker side
```

## plan_context hook (`prebuilt/plan_context.py`)

`inject_plan_context()` registered on `"before_agent"`. Resolves `work_brief_pointer` from task input, loads the work_brief blob from artifact store, injects parsed plan context into agent kwargs before invocation.

This is the single place where orchestration's pointer-only contract is resolved on the worker side.

## What hooks does NOT own

- Graph-level hooks (`GraphHookRegistry` lives in `monet.orchestration`)
- Agent execution logic
- Queue dispatch

## Invariants

- Hooks run in worker process, not server process
- `inject_plan_context` is idempotent — if no pointer in input, no-op
- New worker hooks: add to `prebuilt/`, register via `@on_hook("before_agent" | "after_agent")`, import in `prebuilt/__init__.py` for side-effects
