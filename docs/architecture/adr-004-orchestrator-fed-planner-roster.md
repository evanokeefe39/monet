# ADR-004: Orchestrator-fed planner roster

**Status:** Accepted
**Date:** 2026-04-18
**Frame:** How does a planner agent see the full-fleet capability roster in split-fleet (S2/S3) deployments where its local worker registry only knows its own pool?

---

## Context

After ADR-003 collapsed agent registration onto `CapabilityIndex`, the
`@agent` decorator writes only the local registry. The reference
planner agent (`src/monet/agents/planner/__init__.py`) renders an
"available agents" roster in its prompt so the LLM can compose a plan
that dispatches to real, registered capabilities.

In the monolith (S1) the worker and server share one process and the
same registry, so local reads suffice. In split-fleet (S2/S3) and SaaS
(S5) the planner runs inside a worker whose local registry only holds
that pool's capabilities — planning against it would silently omit
every other pool.

Two designs were considered:

1. **Agent queries the server at runtime.** Planner calls a
   `fleet_capabilities()` helper that hits `GET /api/v1/agents` over
   HTTP using worker-side `MONET_SERVER_URL` + `MONET_API_KEY`.
2. **Orchestrator feeds the roster via task context.** The
   orchestration-side `planner_node` reads the in-process
   `CapabilityIndex` and appends an
   `{"type": "agent_roster", "agents": [...]}` entry to the planner's
   task context before enqueueing.

---

## Decision

**Orchestrator-fed.** `src/monet/orchestration/planning_graph.py::
_roster_context_entry()` snapshots the CapabilityIndex and appends an
`agent_roster` context entry inside `_invoke_planner`. The planner's
`_build_roster(context)` reads the entry when present and falls back to
`default_registry` when absent (unit tests, library callers).

---

## Rationale

### Agent stays transport-agnostic

A planner that speaks HTTP and reads env vars is no longer a pure
`task + context → output` function. Every meta-agent that ever needs
fleet data would inherit the same coupling. Keeping the planner pure
preserves the invariant that agents are drop-in handlers, identical
whether invoked from orchestration, a notebook, a test harness, or a
standalone driver.

### Context is already the input surface

Agents already consume typed context entries (`instruction`,
`user_clarification`, `upstream_result`, `artifact`). Adding
`agent_roster` is one more member of an existing vocabulary — not a
new coupling surface.

### Policy belongs in orchestration, not route handlers

Tenant scoping, permission filtering, deprecation hiding, relevance
ranking, pool-health enrichment — these are all policy decisions that
match the orchestration layer's scope. Feeding the roster through
orchestration gives one place to curate. The HTTP route would have
to carry the same logic, mixing capability lookup with orchestration
policy.

### No per-revise HTTP hops

The planning subgraph revises plans up to `PLAN_MAX_REVISIONS=3` times;
each revise re-invokes the planner. Orchestrator-fed reads the
in-process `CapabilityIndex` on each visit — one dict copy. Agent-query
would HTTP-roundtrip 3× per plan, plus complicate cost attribution.

### Monolith parity

S1 and S2 both go through `_invoke_planner` → `_roster_context_entry`
→ `CapabilityIndex.capabilities()`. No fast-path hack, no "are we
monolith?" branch. Same code everywhere; only the data population
mechanism differs (in-proc heartbeat in S1, HTTP heartbeat in S2/S3).

---

## Tradeoffs accepted

- **Context-shape coupling.** Orchestrator and planner agree on the
  `{"type": "agent_roster", "agents": [{agent_id, command, description}]}`
  contract. Adding a field (e.g. `pool`) is a coordinated change. We
  accept this — context is already a coordinated contract.
- **Envelope size.** Each planner enqueue serializes the full roster
  into the task record. At 100s of capabilities this is kilobytes.
  Mitigation if/when it bites: orchestrator can prune before injection
  (top-N by relevance). Only meta-agents receive rosters; worker
  agents carry no extra payload.
- **Standalone planner invocation needs context.** Library users
  invoking `planner_plan(task, context)` directly without orchestration
  must supply their own roster or accept the local-registry fallback.
  Acceptable — those users are already hand-constructing the whole call.

## Tradeoffs rejected (agent-query path)

- **Agent knows transport.** Planner would import `httpx`, resolve
  `MONET_SERVER_URL`, handle 401/503/timeout. "Pure handler" invariant
  weakens for the 5% of meta-agents.
- **Policy split.** Tenant scoping would have to live in
  `GET /api/v1/agents` route rather than orchestration.
- **Testability regression.** Every planner test would need `httpx`
  mocking.
- **Monolith waste.** S1 loopback HTTP to same-process server, or a
  fast-path check that reintroduces the ContextVar-handle indirection
  ADR-003 rejected.

---

## Contract

Orchestration-side:

- Before invoking `planner`, append an `agent_roster` entry to
  `context` snapshotting the current `CapabilityIndex`. Shape:
  ```python
  {
      "type": "agent_roster",
      "summary": f"{n} agent(s) available across the fleet",
      "agents": [
          {"agent_id": str, "command": str, "description": str},
          ...
      ],
  }
  ```
- Skip injection when `get_capability_index()` returns None or the
  index is empty — the planner falls back to local registry.

Agent-side:

- `_build_roster(context)` MUST scan `context` for an `agent_roster`
  entry first and return its `agents` list (sorted) when present.
- Fall back to `default_registry.registered_agents(with_docstrings=True)`
  when no entry is present. Useful for unit tests and direct invocation.

---

## Follow-ons

- **Other meta-agents.** `data_analyst(score_agents)` and any future
  manager/scorer agent should consume the same `agent_roster` context
  entry. Not yet wired — follow up when the recruitment pipeline's
  orchestration graph is formalised.
- **Enriched roster.** If the planner starts wanting pool health,
  cost-per-call, or recent error rates, those fields live on the
  injected entry — orchestrator computes, planner consumes.
- **Tenant scoping.** When Priority 1 SaaS primitives land, the
  orchestrator filters the roster by tenant at injection time.

---

## References

- ADR-003 — agent-registration collapse (established `CapabilityIndex`).
- `src/monet/orchestration/planning_graph.py::_roster_context_entry`
- `src/monet/agents/planner/__init__.py::_build_roster`
- Earlier commit-history debate in session 2026-04-18.
