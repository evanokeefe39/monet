# What is monet?

monet is a multi-agent orchestration SDK for Python. It provides the trust infrastructure that makes autonomous AI agents deployable in environments where safety, auditability, and human oversight are non-negotiable.

## The problem

Autonomous AI agents are powerful. They manage email, run commands, browse the web, extract data, and automate workflows. Frameworks like OpenClaw have 200k+ GitHub stars because people want agents that actually do things.

But capability and authority are coupled inside these agents. The process that thinks about deleting your email is the same process that holds IMAP credentials. The agent that drafts a reply can also send it. Safety instructions live in the prompt — and can be dropped by context compaction, bypassed by prompt injection, or ignored by hallucinated tool calls.

The result: 137 security advisories in two months for the most popular agent framework. A CVSS 9.9 privilege escalation. A Meta AI safety director losing 200+ emails when her agent's safety instruction was compacted out. CrowdStrike selling detection-and-removal tooling. 63% of exposed instances with no authentication. Enterprise IT blocking adoption entirely.

The industry response splits into two camps: detect and remove agents (endpoint security), or govern them at runtime via policy interception (Microsoft Agent Governance Toolkit, OWASP guidelines). Neither camp answers the question enterprises actually ask: how do we deploy these agents safely for production work?

## What monet does

monet is the organizational harness where agents are sandboxed, untrusted tenants — flavour of the month until proven otherwise.

Agents are opaque capability units. They can reason, classify, draft, and recommend. They cannot act. Acting happens in separate pipeline nodes with separate credentials, gated by structural checkpoints the agent doesn't control, can't skip, and can't see.

Safety is a topological property of the system, not a behavioral property of the agent. You can't prompt-inject your way past a pipeline node that runs in a different process.

### Core capabilities

**Pipeline orchestration.** DAG-based execution with typed state, fan-out parallelism, and conditional flow. A planner decomposes work into a static DAG. An execution engine traverses it. Each node is an agent invocation with scoped inputs and outputs.

**Credential isolation.** Different pipeline nodes hold different credentials. The agent that reads your inbox has IMAP read-only. The agent that sends email has SMTP. The agent that thinks about what to do has neither. No single agent has both reasoning capability and destructive authority.

**Structural HITL.** Human-in-the-loop gates are pipeline nodes, not prompt instructions. They can't be compacted out of context, bypassed by injection, or disabled by the agent via config patches. The gate fires because the DAG says it fires, regardless of what the agent wants.

**Append-only audit.** Every tool call, every agent invocation, every HITL decision is recorded in an OpenTelemetry trace the agent can't modify. The audit trail is outside the agent's process, outside its trust boundary, outside its control.

**Signal routing.** Agents emit typed signals (needs review, escalation, semantic error, capability unavailable). The orchestrator routes them — interrupt the pipeline, retry the node, log and continue. Agents influence but never control routing.

**Artifact store.** Agents exchange work products through a pointer-addressed artifact store. The orchestrator sees pointers, never content. Agents fetch full artifacts only when needed. State stays small and inspectable.

## What monet does not do

**monet is not an agent framework.** It doesn't help you build agents. It helps you deploy them safely. Bring your own agent — OpenClaw, CrewAI, LangChain, custom Python, anything that can read input and produce output.

**monet is not a policy engine.** It doesn't evaluate complex policy rules at sub-millisecond latency. It provides protocol interfaces where policy engines (Microsoft AGT, OPA, Cedar) plug in. monet's default is a YAML allow/block list. Customers replace it with whatever their security team prefers.

**monet is not a sandbox.** It doesn't implement container isolation or syscall filtering. It orchestrates sandboxed containers. Docker, gVisor, Firecracker, E2B, Modal — monet provides the container runtime protocol, customers choose the implementation.

**monet doesn't fix bad agents.** If your agent hallucinates, monet can't stop it from hallucinating. It can stop the hallucination from causing damage — because the agent's output goes through QA reflection and human review before anything executes.

## Core principles

monet's architecture draws from Mario Zechner's pi-agent philosophy and Toyota's production system.

**Build only what you need** (pi-agent). Every feature has a carrying cost. The SDK is a small package. Every abstraction justifies itself against a concrete use case.

**Agents are untrusted black boxes** (A2A + pi-agent). The orchestrator preserves agent opacity. Agents collaborate without exposing internals. The harness can't enforce agent output quality — only surface problems through QA reflection and human review.

**Safety is topological, not behavioral** (Toyota P5 — jidoka). Build quality in, don't inspect it in. Credential isolation, pipeline gates, and append-only audit are structural properties of the system. They don't depend on the agent remembering to behave.

**Pull systems** (Toyota P3). Each expansion of trust and blast radius is pulled by demonstrated success — observable track record in the harness. Never pushed by sales or assumed by default.

**Proven technology** (Toyota P8). Python, LangGraph, SQLAlchemy, FastAPI, OpenTelemetry, Docker. No unproven dependencies in the critical path.

**Full observability is non-negotiable** (pi-agent). You must see exactly what goes into the model's context, what came out, and what tools were called. OTel spans fire at every agent invocation.

**Minimal stable interfaces** (pi-agent). The agent interface is one decorator, one input envelope, one output envelope. The orchestrator sees only the output envelope. These interfaces are designed to not change.

## Extension model

monet's value is the harness topology. Everything else is pluggable.

### Tier 1 — Harness core (monet owns)

Pipeline topology. DAG execution. Artifact handoff via pointers. HITL as a structural gate. Abort as container kill. Signal routing. These properties make the harness a harness. Not pluggable — if you swap these, you have a different system.

### Tier 2 — Default implementations (monet ships, customer replaces)

Each layer has a protocol interface. monet ships a working default. Customers swap for their preferred tool.

| Layer | monet default | Customer replaces with |
|---|---|---|
| Policy evaluation | YAML allow/block list | Microsoft AGT, OPA, Cedar |
| Container runtime | Docker | gVisor, Firecracker, E2B, Modal |
| Audit sink | OTel spans + event store | Langfuse, Datadog, Splunk, Wiz |
| Identity / auth | API key | Okta, Azure AD, identity-aware proxy |
| HITL routing | Telegram / Discord | Slack, PagerDuty, ServiceNow |
| Approval policy | Gate all destructive actions | Risk-scored auto-approval, compliance rules |

### Tier 3 — Customer-added layers (monet provides hooks)

Worker hooks (`@on_hook("before_agent")`) and graph hooks (`GraphHookRegistry`) are extension points. Customers add their own pre/post processing without modifying monet core:

- DLP scanning on artifacts before they leave the data plane
- Cost tracking per agent invocation
- Custom signal types from domain-specific QA agents
- Compliance tagging per run (SOC2 control mapping)
- Rate limiting per agent, user, or team

## Split-plane architecture

monet separates orchestration from data by construction.

```
Control plane                    Data plane
(scheduling, routing,            (execution, artifacts,
 HITL decisions, abort)           events, telemetry)
         │                              │
         │  pointers only               │  customer data
         │  no customer content         │  never leaves customer infra
         │                              │
    ┌────┴────┐                    ┌────┴────┐
    │  Server  │                    │ Workers  │
    │ (Aegra)  │◄──── API key ────►│ + Agents │
    └─────────┘                    └─────────┘
                                        │
                                   ┌────┴────┐
                                   │  Audit   │
                                   │  Store   │
                                   └─────────┘
```

The control plane sees skeletons and pointers. Customer telemetry and artifacts never traverse vendor infrastructure. For self-hosted deployments, both planes run on the same machine — zero config change, unified URL.

## Progressive adoption

Trust grows with blast radius. Each step is pulled by demonstrated success — observable track record in the harness, not a sales pitch.

**Step 1 — Personal worker.** `monet dev` on your laptop. Agent runs in a hardened container on the same machine. Blast radius: your machine. Data never leaves. Entry cost: zero infrastructure. Weeks of OTel traces build a track record.

**Step 2 — Team workers.** Multiple users, each running `monet worker` on their own machines, connecting to a shared server. Each person's worker processes only tasks routed to their pool. Langfuse dashboard shows every agent's track record side by side. Trust becomes observable by the team.

**Step 3 — Orchestration SaaS.** Control plane hosted. Data plane stays on customer machines. Customer points OTel at their own Langfuse/Datadog. Enterprise IT can audit without touching the control plane. Abort is a control-plane operation that kills the worker-side container.

**Step 4 — Centralized fleet.** Track record justifies moving workers off laptops onto VPS or cloud. Push pools (ECS/Cloud Run dispatch) for centralized management. Security team manages the worker fleet.

Each transition is a configuration change, not a migration. Same code, same pipelines, same agents at every step.

## What's possible today

- Define agents with `@agent` decorator — dual call signatures, typed context injection, signal emission
- Orchestrate multi-agent pipelines — planning → execution with wave-based parallelism and QA reflection
- Run distributed — server + remote workers with pool-based routing and cloud dispatch (ECS, Cloud Run)
- Store and retrieve artifacts — pluggable backends (filesystem, S3, GCS, Postgres)
- Gate destructive actions — HITL interrupts with form-schema conventions
- Observe everything — OTel spans at every boundary, Langfuse integration, structured logging
- Chat interface — Textual TUI with streaming, HITL rendering, slash commands

## Future roadmap

- **Organizational harness MVP** — OpenClaw as first sandboxed tenant, MCP tool bridge, container hardening templates, prompt injection demo, progressive trust demo
- **Extension protocols** — formalized interfaces for policy evaluation, container runtime, HITL routing, audit sink, identity
- **Scheduled runs** — cron-style triggers against configured entrypoints
- **SaaS enabling primitives** — pluggable auth, tenant scoping, credential passthrough
- **Memory service** — long-lived agent memory, pointer-addressed, semantic retrieval
- **Agent marketplace integration** — plugin lifecycle management via Tier 3 hooks

## Alignment with industry standards

| Standard | monet coverage |
|---|---|
| OWASP Agentic Top 10 (2026) | Structural coverage of ASI01 (goal hijacking — pipeline topology), ASI02 (tool misuse — allow/block policy), ASI03 (identity abuse — credential isolation), ASI08 (cascading failures — signal routing + abort), ASI10 (rogue agents — container kill + HITL gates) |
| NIST AI RMF | Observability (OTel), human oversight (HITL), risk management (signal routing), governance (audit trail) |
| Gartner guidance | Agents as first-class identities (pool assignment), least-privilege (scoped credentials), behavioral monitoring (OTel), audit controls (append-only event store) |
| CrowdStrike OpenClaw guidance | Bind to localhost (container network isolation), require auth (API key), disable high-risk tools (policy), run in Docker read-only (container hardening) |
