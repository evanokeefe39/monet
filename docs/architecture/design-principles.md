# Design Principles

monet's architecture draws from two sources: Mario Zechner's pi-agent design philosophy and Toyota's production system principles. This page summarises the key ideas that shaped the system. For the full treatment, see [SPEC.md](https://github.com/evanokeefe39/monet/blob/master/SPEC.md).

## From pi-agent

**Build only what you need.** Every feature has a carrying cost in context, complexity, and maintenance. The SDK is a small package with no mandatory dependency on LangGraph or any orchestration framework. The catalogue wraps fsspec and SQLAlchemy rather than adopting a heavier platform. Every abstraction justifies itself against a concrete use case.

**Full observability is non-negotiable.** You must see exactly what goes into the model's context, what came out, and what tools were called. OTel spans fire at every agent invocation. Agent sessions are inspectable. Artifacts carry full provenance.

**Minimal stable interfaces.** The agent interface is two endpoints, one input envelope, two output envelopes. The node wrapper sees only `AgentResult`. The orchestrator sees only the output envelope. These interfaces are designed to not change.

**Context engineering is the real work.** Content limits prevent context bloat in graph state. Summaries and pointers keep the orchestrator light. Agents fetch full artifacts from the catalogue only when needed.

**Composability through layering.** Six layers, each ignorant of the others' internals. The SDK has no LangGraph dependency. The orchestrator has no knowledge of agent runtimes. The catalogue has no knowledge of agents or workflows.

**Sessions are first-class serialisable artifacts.** LangGraph state is persisted via the checkpointer. Artifacts are stored in the catalogue with full provenance. Execution history is reconstructable from these stores.

## From Toyota Way

The Toyota Way principles (from Jeffrey Liker's 2004 book) that apply most directly to the architecture:

| Principle | Expression in monet |
|---|---|
| P2 -- Continuous flow | Wave-based execution: smallest parallel batch that flows without blocking |
| P3 -- Pull systems | Triage pulls complexity into heavier process only when needed. Agents are invoked on demand |
| P5 -- Jidoka | Post-wave reflection gates. Agent signal system. Plan approval interrupt. QA is structural |
| P6 -- Standardised tasks | Uniform input/output envelopes. Node wrapper as standardisation enforcement |
| P7 -- Visual control | `emit_progress()` as live status. OTel traces as execution record |
| P8 -- Proven technology | Python contextvars, LangGraph, SQLAlchemy, Langfuse. No unproven dependencies |
| P12 -- Genchi genbutsu | Planner pulls actual research. Post-wave reflection reads actual outputs |
| P13 -- Nemawashi | Human approval gate before execution. Bounded revision count |
| P14 -- Hansei and kaizen | Kaizen hook fires unconditionally. Observability data feeds continuous improvement |

## Foundational decisions

These architectural decisions apply across the system:

- One uniform interface per agent resolves the MxN contracts problem
- Agents are reusable capability units, not workflow-specific components
- Agent selection is fixed at planning time. Capability availability is validated before dispatch
- HITL is the orchestrator's concern, not the agent's
- All agent signals are informational -- agents influence but do not control routing
- The orchestrator preserves agent opacity -- agents collaborate without exposing internals

## From A2A

- Preserve opacity -- agents are blackboxes with interfaces
- The orchestrator's only opinion is that agents are blackboxes
