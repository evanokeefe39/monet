# Design Principles

monet's architecture draws from two sources: Mario Zechner's pi-agent design philosophy and Toyota's production system principles. This page summarises the key ideas that shaped the system.

## From pi-agent

**Build only what you need.** Every feature has a carrying cost in context, complexity, and maintenance. The SDK is a small package with no mandatory dependency on LangGraph or any orchestration framework. The artifact store wraps fsspec and SQLAlchemy rather than adopting a heavier platform. Every abstraction justifies itself against a concrete use case.

**Full observability is non-negotiable.** You must see exactly what goes into the model's context, what came out, and what tools were called. OTel spans fire at every agent invocation. Agent sessions are inspectable. Artifacts carry full provenance.

**Minimal stable interfaces.** The agent interface is two endpoints, one input envelope, two output envelopes. The node wrapper sees only `AgentResult`. The orchestrator sees only the output envelope. These interfaces are designed to not change.

**Context engineering is the real work.** Content limits prevent context bloat in graph state. Summaries and pointers keep the orchestrator light. Agents fetch full artifacts from the artifact store only when needed.

**Composability through layering.** Six layers, each ignorant of the others' internals. The SDK has no LangGraph dependency. The orchestrator has no knowledge of agent runtimes. The artifact store has no knowledge of agents or workflows.

**Sessions are first-class serialisable artifacts.** LangGraph state is persisted via the checkpointer. Artifacts are stored in the artifact store with full provenance. Execution history is reconstructable from these stores.

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

## The organizational harness

monet is not an agent framework. It is the organizational harness where agents are sandboxed, untrusted tenants. The harness is agent-runtime-agnostic: OpenClaw today, CrewAI tomorrow, custom Python next quarter. The trust infrastructure persists across agent fashions.

### Agents are untrusted by default

Every agent -- whether a monet-native `@agent` function or an external runtime in a container -- is treated as an opaque, potentially hostile process. It can reason, classify, draft, and recommend. It cannot act on external systems. Acting happens in separate pipeline nodes with separate credentials, gated by structural checkpoints the agent doesn't control.

This is not a trust judgment about any specific agent. It is a system property. The harness doesn't know whether the agent is trustworthy, and it doesn't need to.

### Safety is topological, not behavioral

Safety instructions that live in the agent's prompt can be compacted out, prompt-injected around, or disabled by the agent itself (CVE-2026-41349 demonstrated an LLM silently disabling its own execution approval). Policy interception that runs in the same trust boundary as the agent can be circumvented if the agent controls its own configuration.

monet's safety properties are topological -- they emerge from the structure of the pipeline, not the behavior of the agent:

- **Credential isolation**: the thinking agent has no credentials. The acting agent has scoped credentials. No single pipeline node has both reasoning capability and destructive authority.
- **Structural HITL**: human-in-the-loop gates are pipeline nodes, not prompt instructions. They fire because the DAG says they fire, regardless of what the agent wants.
- **Append-only audit**: the trace is emitted by the worker sidecar, outside the agent's process. The agent can't suppress, modify, or delete audit entries.
- **Container kill as abort**: `abort(run_id)` kills the container. No prompt, no negotiation, no context window to be ignored.

This aligns with Toyota P5 (jidoka) -- build quality in, don't inspect it in.

### Progressive trust

Trust and blast radius grow together. Each expansion is pulled by demonstrated success -- observable track record in the harness, never pushed by assumption. An agent earns trust through months of append-only audit data, not through its documentation or its own HITL claims.

This maps to Toyota P3 (pull systems) applied to organizational trust. See `docs/overview.md` for the four-step adoption model.

### The harness is the constant

Agent runtimes are flavour of the month. 214k stars one quarter, 137 security advisories the next. The harness doesn't bet on any specific agent runtime. It provides:

- Pipeline topology (Tier 1 -- not pluggable, this is what makes it a harness)
- Default implementations with protocol interfaces (Tier 2 -- customer replaces)
- Extension hooks for customer-specific layers (Tier 3 -- customer adds)

See `docs/overview.md` for the full extension model.

## Agent quality ownership

- The orchestrator cannot enforce agent output quality -- agents are potentially untrusted black boxes. It provides signal mechanisms for agents to communicate failure and quality concerns. QA agents are the semantic quality layer. If a user brings a research agent that hallucinates, monet's framework cannot fix this design flaw -- only surface it through QA reflection and human review gates
- Good agent citizenship means: validate your own output, raise `EscalationRequired` on unrecoverable failure, emit appropriate signals for quality concerns. The decorator catches empty results structurally, but only the agent knows whether non-empty content is actually useful
- Three lines of defense: agent self-validation (agent's job), QA reflection gates (orchestrator's job), human review at interrupts (human's job)
