# Server Contract Specification

## Status

The circular import problem described below has been resolved. This document records the original problem and the contracts that were established to fix it.

**Shipped:** `contracts/` is now a zero-import foundation package. `PoolConfig` lives in `monet.config._pools`. `task_hmac` lives in `monet.core.auth`. The old webhook push model (`push_handler.py`, `_push_with_retry`, `close_dispatch_client`) has been removed and replaced by the outbound-only `DispatchBackend` protocol.

## Problem Statement (historical)

`monet.server` and `monet.orchestration` had circular imports through private symbols:

```
server._aegra_routes  →  orchestration._invoke.configure_capability_index
server._aegra_routes  →  orchestration._invoke._push_with_retry (private)  [removed]
server._aegra_routes  →  orchestration._invoke._write_dispatch_failed (private)  [removed]
server.__init__       →  orchestration._invoke.close_dispatch_client  [removed]
server.server_bootstrap → orchestration._invoke.get_queue
server.server_bootstrap → orchestration.configure_queue
server._routes        →  orchestration._invoke.invoke_agent

orchestration._invoke →  server._auth.task_hmac  [moved to core.auth]
orchestration._invoke →  server._config.load_config (PoolConfig)  [moved to config._pools]
```

This created:
- Fragile coupling to private implementation details
- Circular import risk (deferred via local imports)
- No testable contract boundary
- Impossible to mock orchestration in server tests without patching internals

## Design Principle

**Dependency direction:** `server` depends on `orchestration` (public protocol only). `orchestration` depends on `core` and `queue`. Neither should reach into the other's private symbols.

Shared primitives that both need (`task_hmac`, `PoolConfig`) belong in a lower layer that both can import.

## Proposed Architecture

```
monet.core.auth        ← task_hmac (new home, shared by server + orchestration)
monet.config           ← PoolConfig + load_pool_config (relocated from server._config)
monet.queue            ← TaskQueue protocol (extended with ping/backend_name)
monet.orchestration    ← OrchestrationService protocol (new public surface)
monet.server           ← depends on all above; owns HTTP transport + capability index
```

## Contract 1: TaskQueue Protocol Extension

Add to `monet.queue._interface.TaskQueue`:

```python
class TaskQueue(Protocol):
    # ... existing 7 methods ...

    async def ping(self) -> bool:
        """Check backend connectivity. Returns True if healthy.

        Default: True (in-memory is always healthy).
        Eliminates isinstance(queue, RedisStreamsTaskQueue) in health checks.
        """
        ...

    @property
    def backend_name(self) -> str:
        """Human-readable backend identifier (e.g. 'redis', 'memory').

        Used in health responses and logging. Eliminates type(queue).__name__.
        """
        ...
```

**Removes:** All `isinstance(queue, RedisStreamsTaskQueue)` in `_routes.py` health endpoint.

## Contract 2: Push Recovery Port

Push dispatch and recovery is currently split between `orchestration._invoke` (retry logic) and `server._aegra_routes` (boot recovery orchestration). The server needs to call retry/fail during boot recovery, but shouldn't know the implementation.

```python
# monet.orchestration._push (new module, public within orchestration)

class PushDispatcher(Protocol):
    """Push dispatch operations available to server boot recovery."""

    max_attempts: int

    async def retry(
        self,
        task_id: str,
        queue: TaskQueue,
        url: str,
        headers: dict[str, str],
        envelope: dict[str, Any],
        task_payload: str,
        *,
        dispatch_secret: str | None = None,
    ) -> None:
        """Retry a push dispatch. Raises PushDispatchTerminal on exhaustion."""
        ...

    async def fail(
        self,
        task_id: str,
        queue: TaskQueue,
        detail: str,
    ) -> None:
        """Write DISPATCH_FAILED result and clean up tracking record."""
        ...
```

Exposed via `monet.orchestration`:

```python
from monet.orchestration import get_push_dispatcher
dispatcher = get_push_dispatcher()
```

## Contract 3: OrchestrationService

What the server route layer needs from orchestration:

```python
# monet.orchestration — public surface

class OrchestrationService(Protocol):
    """Operations the server HTTP layer may invoke."""

    async def invoke_agent(
        self,
        agent_id: str,
        command: str,
        task: str = "",
        context: list[dict[str, Any]] | None = None,
        skills: list[str] | None = None,
        thread_id: str | None = None,
    ) -> AgentResult:
        """Dispatch a single agent invocation via the task queue."""
        ...

    def configure_queue(self, queue: TaskQueue) -> None:
        """Install the process-wide task queue."""
        ...

    def get_queue(self) -> TaskQueue | None:
        """Return the currently configured queue, or None."""
        ...

    def configure_capability_index(self, index: CapabilityIndex) -> None:
        """Install the capability index for cross-pool routing."""
        ...

    async def close(self) -> None:
        """Shut down orchestration resources (dispatch client, etc)."""
        ...
```

The server calls these through the public `monet.orchestration` module — never through `_invoke` directly.

## Contract 4: Shared Auth Primitives

`task_hmac` is needed by both:
- Server (`_auth.py`) — to validate incoming task auth
- Orchestration (`_invoke.py`) — to mint tokens for push dispatch

It belongs in neither. Move to `monet.core.auth`:

```python
# monet/core/auth.py
def task_hmac(api_key: str, task_id: str) -> str:
    """Derive per-task HMAC bearer. HMAC_SHA256(api_key, task_id).hexdigest()."""
    return hmac.new(api_key.encode(), task_id.encode(), hashlib.sha256).hexdigest()
```

Both `server._auth` and `orchestration._invoke` import from `monet.core.auth`.

## Contract 5: Pool Configuration

`PoolConfig` and `load_config` currently live in `server._config` but orchestration needs them for push routing. Move to `monet.config`:

```python
# monet/config/_pools.py (or inline in monet/config/__init__.py)
@dataclass(frozen=True)
class PoolConfig:
    name: str
    type: Literal["local", "pull", "push"]
    lease_ttl: int = 300
    url: str | None = None
    auth: str | None = None
    dispatch_secret: str | None = None

def load_pool_config(path: Path | None = None) -> dict[str, PoolConfig]:
    """Load pool topology from monet.toml + environment."""
    ...
```

Server's `_config.py` becomes a thin re-export or is deleted.

## Contract 6: CapabilityIndex Ownership

The `CapabilityIndex` is owned by the server — it is populated by HTTP heartbeats and consumed by HTTP routing. Orchestration needs read access (for `get_pool`) but should not own the instance.

Current pattern (bidirectional wiring via `configure_capability_index`) is acceptable but should be the *only* coupling point, accessed via the public `OrchestrationService.configure_capability_index()`.

The `CapabilityIndex` class itself stays in `monet.server._capabilities`. Orchestration imports only the type (for TYPE_CHECKING) and receives the instance via the configure call.

## Migration Steps

### Phase 1: Shared primitives (no behavior change)

1. Create `monet/core/auth.py` with `task_hmac`
2. Move `PoolConfig` + `load_pool_config` to `monet/config/_pools.py`
3. Re-export from old locations for backwards compat (one release)
4. Update imports in `_invoke.py` and `_auth.py`

### Phase 2: TaskQueue protocol extension

1. Add `ping()` and `backend_name` to `TaskQueue` protocol
2. Implement in `InMemoryTaskQueue` (ping=True, backend_name="memory")
3. Implement in `RedisStreamsTaskQueue` (existing ping, backend_name="redis")
4. Replace isinstance checks in `_routes.py` health endpoint
5. Replace isinstance checks in `__init__.py` lifespan

### Phase 3: Public orchestration surface

1. Add `close_dispatch_client` to `monet.orchestration.__init__` exports
2. Add `get_push_dispatcher()` returning the push recovery interface
3. Replace server's `from monet.orchestration._invoke import ...` with public imports
4. `invoke_agent` is already exported — just use it via the public path

### Phase 4: Eliminate remaining isinstance for Redis-specific ops

The reclaim loop and push recovery use Redis-specific methods (`reclaim_expired_internal`, `record_push_dispatch`, etc.). These are intentionally NOT on the protocol (per queue CLAUDE.md). Two options:

**Option A (recommended):** Add an optional `MaintenanceQueue` protocol:
```python
class MaintenanceQueue(Protocol):
    async def reclaim_expired(self) -> list[str]: ...
    async def list_in_flight_push_dispatches(self) -> list[dict]: ...
    async def record_push_dispatch(...) -> None: ...
    async def pop_push_dispatch(task_id: str) -> None: ...
```

Server checks `isinstance(queue, MaintenanceQueue)` — still isinstance but against a protocol, not a concrete class. In-memory backend doesn't implement it. Redis does.

**Option B:** Keep isinstance against `RedisStreamsTaskQueue` for maintenance ops only. Accept this as a known concession documented here.

## What This Solves

| Before | After |
|--------|-------|
| 8 private symbol imports across boundary | 0 private imports |
| Circular dependency (server ↔ orchestration) | Unidirectional (server → orchestration) |
| Can't test server without orchestration internals | Mock `OrchestrationService` protocol |
| `task_hmac` in wrong layer | Shared in `core.auth` |
| `PoolConfig` in wrong layer | Shared in `config` |
| isinstance on concrete Redis class in routes | Protocol method (`ping`, `backend_name`) |
| No documentation of coupling contract | This document |

## Non-Goals

- Moving `CapabilityIndex` out of server (it's correctly placed)
- Abstracting the Aegra integration (it's deployment-specific glue)
- Changing the queue protocol's 7 core methods
- Adding push dispatch to the core queue protocol (it's transport-specific)
