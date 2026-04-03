# Agent Implementations

Reference implementations for the six agents in the monet system. Each agent is a capability unit decorated with `@agent`, implemented using pi as the default runtime. Domain specialisation comes from skills loaded at invocation time. Behavioural depth comes from extensions loaded based on command and task complexity. The base agent is thin — skills and extensions carry the domain weight.

These are reference designs, not prescriptions. The agent interface contract — input envelope in, output envelope out — is what matters. The internal implementation is the agent developer's domain.

---

## Planner

**Role**: The translation layer between vague user intent and structured, executable work briefs. The first agent invoked for any complex request. Responsible for ensuring the system never works from ambiguous or misunderstood requirements.

**Commands**

`fast` — Quick triage assessment. Classifies a user message as simple, bounded, or complex. Returns a structured triage decision with complexity classification, suggested agents, and whether full planning is required. Used by the triage node before deciding whether to invoke the full planning graph. Synchronous, bounded, no catalogue artifacts.

`plan` — Full work brief production. Iterative. May signal that it needs research or analysis before committing to a plan (via `needs_human_review` with a structured reason that specifies which agent to call and what to ask). On completion writes the approved work brief to the catalogue as a structured artifact. The work brief is the most important artifact in the system — it drives all downstream routing decisions.

**Extensions**

- Thinking — active on `plan` command. Pre-commitment reasoning before producing the brief prevents the planner from committing to the first plausible interpretation.
- Todo — active on `plan` command for complex planning sessions that span many tool calls.

**HITL**

Always interrupts after producing a draft work brief for human approval. This is a structural checkpoint — it fires regardless of agent signals. The human sees the full plan including phases, dependency waves, agent assignments, quality criteria, and all assumptions the planner made in translating user intent. Nemawashi gate: slow consensus before fast execution.

On revision request the planner receives the human's feedback as a typed `instruction` context entry and produces a revised draft. Bounded by a configured maximum revision count — if consensus cannot be reached, the process terminates with an escalation rather than either party capitulating.

**Work brief schema**

Universal layer (all work briefs):

| Field | Description |
|---|---|
| `goal` | Single clear outcome statement |
| `in_scope` | What is explicitly included |
| `out_of_scope` | What is explicitly excluded |
| `quality_criteria` | What good looks like — the termination condition for each phase |
| `constraints` | Time, length, format, cost, audience, sensitivity |
| `capability_requirements` | Agent ID, command, and skills per wave item |
| `phases` | Ordered list of phases, each containing dependency waves |
| `human_checkpoint_policy` | Where explicit human approval is required during execution |
| `assumptions` | Every significant interpretive decision made in translating user intent |

Specialist layer (populated for domain-specific work):

| Field | Description |
|---|---|
| `domain_context` | Background the agents cannot be expected to know from base training or skills |
| `evaluation_methodology` | Prescriptive methodology rather than descriptive quality criteria |
| `output_schema` | Specific structured format required in the output |
| `acceptance_tests` | Concrete verifiable conditions for sufficiently specialised work |

**Skeleton**

```python
@agent(agent_id="planner")
async def planner_fast(task: str):
    """
    Classify a user message as simple, bounded, or complex.
    Returns a structured triage decision with complexity level,
    suggested agents, and whether full planning is required.
    """
    ...

@agent(agent_id="planner", command="plan")
async def planner_plan(task: str, context: list):
    """
    Produce a structured work brief from user intent. Decomposes the
    goal into phases and dependency waves with agent assignments,
    quality criteria, and explicit assumptions. May signal that
    research or analysis is needed before the plan can be committed.
    """
    ...
```

---

## Researcher

**Role**: Information gathering across any domain to a specified depth. Produces structured research findings as catalogue artifacts. Used by both the planning graph (to inform a plan) and the execution graph (to produce research phases in a work brief).

**Commands**

`fast` — Quick lookup or targeted question against available sources. Returns an inline answer. Used by the planning graph when the planner needs a specific piece of information to resolve an ambiguity in the work brief. Synchronous, bounded.

`deep` — Exhaustive research across all available sources for a given topic. Produces one or more catalogue artifacts containing findings, citations, confidence-weighted synthesis, and a structured summary. Suitable for research phases in a work brief where comprehensive coverage is required before writing begins. Async, long-running.

**Extensions**

- Todo — active on `deep` command. Tracks research sub-tasks across a long invocation. Prevents context loss on sessions that span many search and synthesis cycles.
- Context compression — active on `deep` command. Manages session length for exhaustive research that accumulates many tool results.
- Tool result size — active on all commands. Intercepts large search and fetch results before they reach the LLM context, offloads to catalogue, passes summaries instead.

**HITL**

Policy-driven. Interrupts when confidence falls below the threshold declared in the capability descriptor for this command. The interrupt surfaces the specific findings where confidence is low and asks the human whether to continue with caveated results, request additional research depth, or accept the partial output.

**Durable execution**

For long-running `deep` invocations the researcher should implement internal durable execution (Temporal or equivalent) keyed by `run_id`. LangGraph retry becomes a transparent resume rather than a full restart. This is an internal agent concern — the orchestrator's retry policy fires identically regardless.

**Skeleton**

```python
@agent(agent_id="researcher")
async def researcher_fast(task: str, context: list):
    """
    Targeted lookup for a bounded question. Returns an inline answer
    with source citations. Suitable for resolving specific ambiguities
    during planning or for simple bounded research questions.
    """
    ...

@agent(agent_id="researcher", command="deep")
async def researcher_deep(task: str, context: list):
    """
    Exhaustive research across available sources for a given topic.
    Produces catalogue artifacts with findings, citations, and a
    confidence-weighted synthesis. Suitable for research phases
    requiring comprehensive coverage before writing begins.
    """
    ...
```

---

## Analyst

**Role**: Structured reasoning over data and information. Where the researcher gathers information, the analyst interprets it — identifying patterns, drawing inferences, producing assessments, and answering analytical questions. Can be used in the planning graph to inform a complex plan or in the execution graph as a dedicated analysis phase.

**Commands**

`ask` — Ad hoc query against available data or provided context. Returns an inline analytical answer. Synchronous, bounded. Used for targeted analytical questions during planning or for quick interpretation of a specific data point.

`deep-analysis` — Multi-step analysis across structured and unstructured data sources. Produces catalogue artifacts with methodology, findings, confidence scores per finding, and explicit reasoning chains. Suitable for complex analytical questions where a single query is insufficient. Async, long-running.

**Extensions**

- Thinking — active on `deep-analysis` command. Pre-commitment reasoning prevents the analyst from anchoring on the first plausible interpretation of ambiguous data.
- Todo — active on `deep-analysis` command. Tracks analytical sub-tasks across long multi-step analyses.
- Tool result size — active on all commands. Intercepts large data payloads before they reach the LLM context.

**HITL**

Policy-driven. Interrupts when analytical confidence falls below threshold or when the analysis surfaces a material ambiguity that requires domain expertise to resolve. The interrupt surfaces the specific question and the available evidence, asking the human to resolve the ambiguity before the analysis continues.

**Skeleton**

```python
@agent(agent_id="analyst", command="ask")
async def analyst_ask(task: str, context: list):
    """
    Ad hoc analytical query against available data or context.
    Returns an inline answer with explicit reasoning. Suitable for
    targeted questions during planning or quick interpretation tasks.
    """
    ...

@agent(agent_id="analyst", command="deep-analysis")
async def analyst_deep(task: str, context: list):
    """
    Multi-step analysis producing structured findings with methodology,
    confidence scores per finding, and explicit reasoning chains.
    Suitable for complex analytical questions requiring comprehensive
    treatment before downstream decisions can be made.
    """
    ...
```

---

## Writer

**Role**: Structured content production in any domain and format. Consumes research and analysis artifacts as context and produces written output conforming to the work brief's output schema and quality criteria. The writer does not research — it writes from provided context. Separation of research and writing is a deliberate quality design: the writer's full attention is on the writing task.

**Commands**

`fast` — Bounded writing task, returns inline result. Suitable for short-form content, single paragraphs, targeted rewrites, or quick drafts that do not warrant catalogue artifacts.

`deep` — Full content production from provided research and analysis context. Writes to the output schema specified in the work brief. Produces catalogue artifacts — one per logical section or per the work brief's output structure. Async, long-running.

`translate` — Translate existing content into a target language specified in the task. Preserves structure, tone, and domain terminology. Uses the same system prompt and toolset as standard writing commands — translation is a writing task, not a separate capability.

**Extensions**

- Todo — active on `deep` command. Tracks section completion across long writing sessions.
- Context compression — active on `deep` command with large research context. Manages session length for complex documents that require synthesising many research artifacts.

**HITL**

Policy-driven via QA outcome. The writer itself does not interrupt — it writes and returns. If QA rejects the output, the execution graph routes back to the writer with QA's `revision_notes` as a typed `instruction` context entry. The bounded revision count in the execution graph prevents infinite cycles.

**Skeleton**

```python
@agent(agent_id="writer")
async def writer_fast(task: str, context: list):
    """
    Bounded writing task returning inline result. Suitable for short-form
    content, targeted rewrites, or quick drafts not requiring catalogue
    artifacts.
    """
    ...

@agent(agent_id="writer", command="deep")
async def writer_deep(task: str, context: list):
    """
    Full content production from provided research and analysis context.
    Conforms to the output schema and quality criteria in the work brief.
    Produces catalogue artifacts structured per the work brief's output
    specification.
    """
    ...

@agent(agent_id="writer", command="translate")
async def writer_translate(task: str, context: list):
    """
    Translate existing content into the target language specified in the
    task. Preserves structure, tone, and domain terminology. The source
    content is provided as a context entry.
    """
    ...
```

---

## QA Agent

**Role**: Independent quality assessment of content produced by other agents, evaluated against the work brief's quality criteria and acceptance tests. The QA agent never produces content — it evaluates content and returns structured findings. Its independence from the producing agent is architecturally enforced: no agent can bypass QA by suppressing signals, and QA invocation is an orchestrator policy decision, not an agent decision.

**Commands**

`fast` — Post-wave reflection assessment. Reads the wave's expected outputs from the plan and the actual outputs produced, returns a structured quality assessment: pass/fail, confidence score, specific issues found, and revision notes if failing. This is the jidoka checkpoint called after every wave in the execution graph. Synchronous, bounded.

`deep` — Full document quality assessment. Evaluates the complete output against the work brief's quality rubric, output schema, acceptance tests, and domain-specific evaluation methodology. Produces a catalogue artifact with the full assessment including section-by-section findings, confidence per section, and prioritised revision notes. Used for final review before the publisher is invoked. Async.

**Extensions**

- Thinking — active on both commands. Pre-commitment reasoning before reaching a quality judgement prevents the QA agent from anchoring on surface characteristics rather than substantive quality.
- Todo — active on `deep` command. Tracks assessment sub-tasks across long document reviews.

**HITL**

Interrupts when confidence in the quality assessment falls below threshold — meaning QA itself is uncertain whether the output meets the criteria. This surfaces to the human as "QA is uncertain — here is what it found, here is what it could not determine." The human can resolve the uncertainty, override the assessment, or request a deeper evaluation.

Does not interrupt on a clear fail — a clear fail routes back to the writer via the execution graph's routing logic. Only uncertain assessments require human judgment.

**Output structure**

The QA assessment artifact carries structured findings rather than free-form text, enabling the execution graph to route deterministically:

| Field | Description |
|---|---|
| `verdict` | `pass`, `fail`, or `uncertain` |
| `confidence` | Float 0–1 on the verdict itself |
| `issues` | List of structured findings: section, criterion violated, severity |
| `revision_notes` | Prioritised, actionable instructions for the writer |
| `acceptance_tests_passed` | Per acceptance test from the work brief: pass/fail/not-applicable |

**Skeleton**

```python
@agent(agent_id="qa")
async def qa_fast(task: str, context: list):
    """
    Post-wave reflection assessment. Evaluates actual wave outputs
    against the plan's expected outputs and quality criteria for this
    wave. Returns a structured verdict with specific issues and
    revision notes if failing.
    """
    ...

@agent(agent_id="qa", command="deep")
async def qa_deep(task: str, context: list):
    """
    Full document quality assessment against the work brief's quality
    rubric, output schema, and acceptance tests. Produces a catalogue
    artifact with section-by-section findings, confidence per section,
    and prioritised revision notes.
    """
    ...
```

---

## Publisher

**Role**: Format transformation and platform optimisation. The final agent in the execution graph. Consumes QA-approved content and transforms it into the target format and platform specification. Does not edit content — formatting and platform adaptation only. The separation of writing and publishing is deliberate: the writer owns quality, the publisher owns format.

**Commands**

`plan` — Produce a publishing plan from the approved content and the work brief's publication constraints. Identifies required format transformations, platform-specific adaptations, and any content elements that cannot be automatically transformed and require human decision. Returns an inline publishing plan. Used as the first step before `publish` when the publication involves multiple targets or complex transformations.

`publish` — Execute the publishing plan. Transforms approved content into the target format, applies platform-specific adaptations, and writes the final publication artifacts to the catalogue. Returns pointers to the published artifacts. Async.

**Extensions**

- Todo — active on `publish` command for multi-target publications. Tracks transformation completion per target.

**HITL**

Always interrupts before the `publish` command executes. This is a structural checkpoint — it fires regardless of agent signals. The human reviews the publishing plan, the target formats, and any unresolvable transformation decisions surfaced by the `plan` command. Publication is irreversible or difficult to reverse; the structural checkpoint ensures no publication happens without explicit human sign-off.

The `plan` command does not interrupt — it informs. Only `publish` requires structural approval.

**Skeleton**

```python
@agent(agent_id="publisher", command="plan")
async def publisher_plan(task: str, context: list):
    """
    Produce a publishing plan from approved content and the work brief's
    publication constraints. Identifies required format transformations,
    platform-specific adaptations, and any decisions requiring human
    input before publication can proceed.
    """
    ...

@agent(agent_id="publisher", command="publish")
async def publisher_publish(task: str, context: list):
    """
    Execute the publishing plan. Transform approved content into target
    formats, apply platform-specific adaptations, and write final
    publication artifacts to the catalogue. Always preceded by human
    approval of the publishing plan.
    """
    ...
```

---

## Cross-Cutting Design Decisions

**Skills drive domain specialisation**

None of the six agents carry domain knowledge in their system prompts. Domain knowledge lives in skill files — versioned markdown documents loaded into agent context at invocation time, specified in the work brief's capability requirements. A researcher working on a medical brief loads medical research methodology skills. A writer working on a legal brief loads legal writing conventions skills. The same agent, different skills, different domain behaviour. This is the composability principle applied to agent knowledge.

**Extensions drive behavioural depth**

Thinking, Todo, Context compression, and Tool result size extensions are composable behaviours loaded based on command and task complexity. They are not hardcoded into any agent. An agent author who wants Thinking behaviour on their custom command activates the Thinking extension in their agent's configuration. Extensions are intra-agent concerns invisible to the orchestrator.

**The writer does not research; the researcher does not write**

This separation is intentional and should be maintained. Agents with combined research-and-write capability accumulate context bloat, make slower tool-calling decisions, and produce lower quality outputs on both dimensions. The pipeline pattern — research produces artifacts, writer consumes them — is the quality design, not a convenience.

**QA independence is architecturally enforced**

No agent can direct the QA agent or suppress its invocation. QA is called by the execution graph as an orchestrator policy decision after each wave and before publication. An agent that produces low-quality output and then signals `qa_not_needed` is not honoured — the orchestrator's policy is the authority, not the agent's signal.

**The planner never executes; executing agents never plan**

The planner produces work briefs. The execution graph executes them. If something unexpected happens during execution, the execution graph interrupts for human input — it does not autonomously replan. Autonomous replanning during execution is a planning graph concern. This boundary prevents the execution graph from accumulating planning logic and the planner from accumulating execution concerns.

---

## Onboarding existing agents

Developers bringing an existing agent into the monet system — a compiled CLI, an HTTP service, a pi agent, a Claude Code skill set, or any agent with documented entry points — do not need to write SDK integration code from scratch. The onboarding mechanism generates a monet client for the existing agent by reading its documentation and producing decorated stub functions that wrap its native entry points.

See [agent-onboarding.md](./agent-onboarding.md) for the full onboarding protocol, available prompt templates, and guidance on reviewing and completing the generated output.
