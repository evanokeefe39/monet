# monet.core — Agent SDK Core

## Responsibility

Agent-side SDK machinery. The `@agent` decorator, registry, context injection, serialization, auth, worker-side hooks, and OTel tracing.

## Key modules

| Module | Owns |
|--------|------|
| `engine.py` | `execute_task()` + `enter_agent_run()` — full agent runtime: ContextVar setup, lifecycle events (agent_started/completed/failed/hitl_cause), OTel spans, hooks, param injection, result wrapping, exception translation, timeout, queue complete/fail. Also owns `_record_lifecycle`, `_validate_signature`, `_inject_params`, `_wrap_result`, `_handle_exception`. Boundary: MUST NOT import from `monet.orchestration`, `monet.worker`, or `langgraph`. |
| `decorator.py` | `Agent` class + `@agent` factory — `Agent` is a declaration object carrying config (agent_id, command, pool, allow_empty); `__call__` delegates to `enter_agent_run()`. `agent()` validates, builds `Agent`, registers in `default_registry`. No runtime logic here. |
| `registry.py` | `AgentRegistry` — maps `agent_id → AgentDescriptor`. `default_registry` is the process singleton. |
| `context.py` | `AgentContext` — typed context injected into agent functions. Run ID, trace ID, agent ID, artifact service. |
| `context_resolver.py` | Resolves `AgentContext` from task record at invocation time |
| `_serialization.py` | `TaskRecord` / `AgentResult` wire-format serialization. Schema-versioned. |
| `auth.py` | API key token generation + verification |
| `hooks.py` | `@on_hook("before_agent" | "after_agent")` — worker-process hooks registry |
| `tracing.py` | OTel span helpers, trace continuity across queue hops |
| `artifacts.py` | `write_artifact()` / `read_artifact()` — agent-callable helpers, auto-inject context |
| `stubs.py` | Test stubs for agent invocation |

## Signals

- Non-fatal: `emit_signal()` appends to `list[Signal]` in result
- Fatal: raise exception — orchestrator treats as agent failure

## What core does NOT own

- Queue transport (that's `monet.queue`)
- Orchestration graphs (that's `monet.orchestration`)
- Config loading (callers pass config)

## Invariants

- No ContextVar indirection — use framework context-local functions directly
- `default_registry` is process-singleton; tests that roll it back must call `register_reference_agents()` to restore
- All public functions have type annotations (py.typed marker)
