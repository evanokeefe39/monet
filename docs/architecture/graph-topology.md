# Graph Topology

## Overview

The system is structured as three graphs with clean handoffs between them. Each graph has a single, focused responsibility and is independently testable, restartable from its own checkpoint, and independently deployable.

## Entry point -- triage

Every user message enters through a lightweight triage node. The triage node calls the planner in `fast` mode to classify the request and return a structured decision.

```
user message
     |
     v
  [triage: planner/fast]
     |
     |-- simple ----------> [responder] --> END
     |                       (direct answer, no agent)
     |
     |-- bounded ----------> [direct agent, fast] --> END
     |                       (one agent, no planning)
     |
     +-- complex ----------> [PLANNING GRAPH]
```

Simple requests get a direct response. Bounded requests invoke a single agent without a work brief. Complex requests enter the planning graph. This prevents planning overhead from being applied to every message.

## Planning graph

Handles iterative plan construction with agent assistance and the human approval gate.

```
START
  |
  v
[planner/plan] <-------------------------------------+
  |                                                    |
  | needs_research or needs_analysis signal            |
  +--> [researcher/fast or analyst/ask] ---------------+
  |    (pull by need, fast mode only, bounded)
  |
  | plan_ready signal
  v
[human approval interrupt] <-- nemawashi gate
  |
  |-- approved ----------> END -> execution graph
  |
  |-- revise -------------> [planner with feedback]
  |                         (bounded revision count)
  |
  +-- reject -------------> END -> kaizen hook
```

The planner signals which agents it needs via structured output. The graph routes to those agents and feeds their output back as typed context entries. The planner remains a blackbox.

The human approval interrupt is the nemawashi gate. The human sees the full plan: phases, dependency waves, agent assignments, quality criteria, and the planner's stated assumptions. If convergence cannot be reached within a configured maximum revision count, the process terminates with an escalation.

### Plan structure

Plans decompose work into phases. Each phase contains dependency waves -- groups of agent tasks with no dependencies within the wave that can execute in parallel.

```
Plan
|-- Phase 1: Research
|   |-- Wave 1: [researcher/deep market, researcher/deep competitors]  (parallel)
|   +-- Wave 2: [analyst/deep-analysis trends]  (depends on wave 1)
|
|-- Phase 2: Writing
|   |-- Wave 1: [writer/deep executive summary]  (depends on phase 1)
|   +-- Wave 2: [writer/deep sections 1-3, writer/deep sections 4-6]  (parallel)
|
+-- Phase 3: Review and Publication
    |-- Wave 1: [qa/deep full document]  (depends on phase 2)
    +-- Wave 2: [publisher/publish]  (depends on wave 1)
```

## Execution graph

A faithful executor of the approved plan. It does not make planning decisions -- unexpected situations trigger interrupts for human input.

```
START (approved plan artifact URL)
  |
  v
[load plan -> identify phases and waves]
  |
  v
[execute wave] --> LangGraph Send API --> parallel agent invocations
  |                                        (one Send per wave item)
  | wave complete
  v
[post-wave reflection: QA/fast]  <-- jidoka checkpoint
  |                                 (reads: plan's expected outputs,
  |                                  actual outputs from wave)
  |-- pass -----------------> next wave or next phase
  |
  |-- retry ----------------> same wave with revision_notes
  |   (within retry limit)
  |
  |-- needs_human_review ----> [interrupt]
  |                             human decides: continue / revise / abort
  |
  +-- fatal ----------------> partial summary -> kaizen hook
  |
  v (all phases complete)
[final synthesis]
  |
  v
END -> kaizen hook
```

**Post-wave reflection** is a quality gate after every wave. The QA agent in `fast` mode receives the wave's expected outputs from the plan and the actual outputs produced. Downstream agents never receive defective inputs from a preceding wave.

**Error handling escalation ladder:**

- `needs_human_review` always interrupts. The human sees full context and chooses to approve, provide corrections, or abort.
- `escalation_requested` interrupts if the human has the required permissions or context.
- `semantic_error` triggers retry up to the configured maximum. Retries exhausted routes to human review. Unrecoverable errors at a blocking dependency terminate the phase.

## Kaizen hook

Fires unconditionally after every execution -- successful, partial, or failed.

```
[final synthesis or partial summary]
  |
  v
[kaizen hook]
|-- compare: planned phases and waves vs actual execution
|-- note: deviations, partial completions, scope changes
|-- record: agent confidence distributions
|-- write: hansei record to kaizen log (catalogue artifact)
+-- return: final message to user
```

The hansei record accumulates across runs. Over time, patterns inform improvements to agent prompts, skill files, quality criteria, and SLA characteristics.
