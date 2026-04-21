# monet.core — Agent SDK Core

## Responsibility

Agent-side SDK machinery. The `@agent` decorator, registry, context injection, serialization, auth, worker-side hooks, OTel tracing, and the RemoteQueue adapter.

## Key modules

| Module | Owns |
|--------|------|
| `decorator.py` | `@agent` decorator — registers into `default_registry`, sets up OTel span, injects context, dispatches command |
| `registry.py` | `AgentRegistry` — maps `agent_id → AgentDescriptor`. `default_registry` is the process singleton. |
| `context.py` | `AgentContext` — typed context injected into agent functions. Run ID, trace ID, agent ID, artifact service. |
| `context_resolver.py` | Resolves `AgentContext` from task record at invocation time |
| `_serialization.py` | `TaskRecord` / `AgentResult` wire-format serialization. Schema-versioned. |
| `auth.py` | API key token generation + verification |
| `hooks.py` | `@on_hook("before_agent" | "after_agent")` — worker-process hooks registry |
| `tracing.py` | OTel span helpers, trace continuity across queue hops |
| `worker_client.py` | `RemoteQueue` — implements `TaskQueue` protocol over HTTP for `monet worker` |
| `artifacts.py` | `write_artifact()` / `read_artifact()` — agent-callable helpers, auto-inject context |
| `_retry.py` | Per-agent retry logic with backoff |
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
