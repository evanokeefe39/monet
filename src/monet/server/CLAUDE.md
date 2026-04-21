# monet.server — FastAPI Application

## Responsibility

FastAPI app factory + HTTP routes. Adapts queue protocol to REST. Capability index for agent discovery. Aegra integration.

## Key modules

| Module | Owns |
|--------|------|
| `__init__.py` | `create_app()` — FastAPI factory, lifespan, sweep loops |
| `_routes.py` | Worker/queue HTTP routes (claim, complete, fail, progress, ping) |
| `_aegra_routes.py` | Aegra custom routes — lifespan bootstrap, push recovery |
| `server_bootstrap.py` | `bootstrap_server()` — instantiates queue, wires singletons. Never at module body level. |
| `_capabilities.py` | `CapabilityIndex` — agent discovery, slash-command vocabulary |

## Aegra constraints

- `server_bootstrap.py` must never create process-singleton state at module body level. Aegra re-executes graph modules under `aegra_graphs.*` namespace — module body runs again. All queue/connection wiring in `bootstrap_server()`, called once from `_aegra_routes._lifespan`.
- New graph builders exported via `server_bootstrap.py` must be wrapped as 0-arg functions (Aegra factory classifier treats 1-arg non-ServerRuntime functions as config-accepting factories).
- `create_app()` is graph-agnostic — no pipeline-specific routes or verbs.

## Routes

Server endpoints must not couple to specific graph topologies. Domain semantics belong in the client layer.

## What server does NOT own

- Agent execution (workers own that)
- Orchestration graph logic
- Config loading (receives `ServerConfig`)
- Queue implementation
