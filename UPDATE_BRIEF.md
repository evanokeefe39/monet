# Monet Platform — Architectural Transition Brief

## Context

Monet is a multi-agent orchestration SDK built on LangGraph Server. It uses a
three-graph supervisor topology: entry/triage → planning (HITL) → wave-based
parallel execution. Agents are invoked through a uniform envelope via
`invoke_agent`. The system is currently monolithic — orchestration and execution
are co-located on the LangGraph server.

The transition decouples orchestration from execution. The mental model is a
**factory floor**: compiled StateGraphs are fixed infrastructure, agents are
machines installed at stations. Machines come and go without rebuilding the
factory.

---

## Core Transition: Orchestration / Execution Separation

**Current:** LangGraph server imports agent modules at startup → decorator side
effects populate `default_registry` → `invoke_agent` calls functions directly.

**Target:** LangGraph server owns graph topology, capability manifest, and task
queue. Workers (local or remote) own handler registries and execution. Workers
poll the task queue, claim tasks with a lease, execute, and post results back.
The orchestration server never sees content — only decisions and pointers.

**Governing principle change:**
- Before: "Agent selection is fixed at graph construction time"
- After: **Graph topology is fixed at construction time. Agent availability is
  verified at dispatch time.**

---

## Decisions Already Made

1. **Pull-based workers.** Workers poll; the server never pushes. Lease-based
   task claiming with TTL. Local workers poll localhost, remote workers poll over
   HTTPS.

2. **Capability manifest replaces in-process registry.** Live map of
   `(agent_id, command) → [worker_id]`. Workers register on connect and
   heartbeat. Lease expiry auto-withdraws capabilities.

3. **Dispatch-time availability checks.** `_assert_registered` at build time is
   removed entirely. `invoke_agent` checks the manifest before dispatch. Missing
   capability → `CapabilityUnavailable` signal → existing wave failure and HITL
   machinery handles it (Jidoka — line stops cleanly at the right station).

4. **Pointer-only state.** Agent outputs never enter LangGraph state or the
   checkpointer. Execution plane serialises to catalogue, returns pointer.
   Orchestration plane holds only `agent_id`, `command`, `success`, `signals`,
   `trace_id`, and catalogue pointer.

5. **`invoke_agent` is the single dispatch seam.** Gains a third branch (queue
   dispatch) alongside existing local and HTTP branches. Nothing above it
   changes.

---

## New Subsystems Required

### CapabilityManifest
```python
class CapabilityManifest:
    def register(self, worker_id: str, capabilities: list[AgentCapability]) -> None
    def deregister(self, worker_id: str) -> None
    def is_available(self, agent_id: str, command: str) -> bool
    def available_for(self, agent_id: str, command: str) -> list[str]
    def snapshot(self) -> dict[str, list[str]]  # health endpoint
```

### Task Queue
Lightweight, SQLite-backed initially. Operations: enqueue, claim (with lease
TTL), complete, fail, requeue expired. Same interface whether worker is local or
remote.

### Worker Sidecar
Thin process developers run in their environment. Authenticates to orchestration
server, polls task queue, invokes agents via local transports (subprocess for
CLIs, direct call for Python, HTTP for remote), serialises output to catalogue,
posts pointer back. The `@agent` decorator and handler registry live here, not
on the orchestration server.

### `monet.server.bootstrap()`
Single function owning startup sequence with guaranteed ordering: tracing →
catalogue → worker listener → manifest init. Called once from `server_graphs.py`.

### `monet.client` module
SDK-exported utilities: `drain_stream`, `get_state_values`, graph node name
constants, typed state initialisers per graph. Removes LangGraph internals from
consumer code.

---

## Module-by-Module Changes

| Module | Action | Notes |
|---|---|---|
| `orchestration/_invoke.py` | Modify | Add manifest check + queue dispatch branch |
| `orchestration/_validate.py` | **Delete** | Build-time checks removed entirely |
| `orchestration/_node_wrapper.py` | Modify | Add `CapabilityUnavailable` signal handling; add `get_stream_writer` instrumentation |
| `orchestration/_content_limit.py` | **Delete (deferred)** | Transitional only; remove once pointer-only model is live |
| `orchestration/_state.py` | Modify | `output` fields become pointer strings; add typed state initialisers as classmethods |
| `orchestration/_run.py` | **Delete** | In-process path not a supported production path; tests use `MemorySaver` directly |
| `orchestration/*_graph.py` | Modify | Remove all `_assert_registered` calls; extract routing functions to `_routing.py`; extract node handlers to per-graph handler modules |
| `monet/_registry.py` | Narrow scope | Remains for worker-side handler registry only; orchestration server uses `CapabilityManifest` |
| `server_graphs.py` (example) | Simplify | Loses `import monet.agents`; becomes two-line declaration calling `bootstrap()` |
| `workflow.py` (example) | Refactor | `_drain_stream` / `_get_state_values` move to `monet.client`; node name strings become SDK-exported constants |
| `app.py` + `server_graphs.py` (example) | DRY | Duplicate catalogue wiring → `CatalogueConfig.from_env()` in SDK |

---

## Where to Start

**`invoke_agent` + `CapabilityManifest`** — highest leverage, unblocks
everything else, requires no changes to graph topology or state schemas.

Sequence:
1. Implement `CapabilityManifest` with in-memory store and heartbeat expiry
2. Add manifest check to `invoke_agent` before dispatch; define
   `CapabilityUnavailable` signal type
3. Add queue dispatch branch to `invoke_agent`
4. Remove `_assert_registered` from graph builders
5. Implement worker sidecar with registration, heartbeat, poll, and result-post
6. Implement pointer-only serialisation on worker side; update state schemas
7. Clean up `server_graphs.py` bootstrap; implement `monet.server.bootstrap()`
8. Extract `monet.client` utilities; fix node name constant leakage

---

## Files to Read First

- `monet/orchestration/_invoke.py` — the dispatch seam
- `monet/orchestration/_node_wrapper.py` — signal handling and stream writer
- `monet/orchestration/_state.py` — state schemas and reducers
- `monet/orchestration/execution_graph.py` — wave fan-out via `Send`
- `examples/social_media_llm/server_graphs.py` — current bootstrap antipattern
- `examples/social_media_llm/workflow.py` — client utilities to extract
- `design-principles.md` — governs all decisions