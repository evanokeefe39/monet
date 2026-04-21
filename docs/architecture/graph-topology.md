# Graph Topology

## Overview

The system is structured as two pipeline graphs (`planning`, `execution`) plus a standalone `chat` graph. Each has a single, focused responsibility and is independently testable, restartable from its own checkpoint, and independently deployable.

Triage is a chat concern, not a pipeline concern. When a caller invokes `monet run <task>` or types `/plan <task>` in chat, they have explicitly committed to planning. There is no entry-time short-circuit that can silently short-circuit that intent — the compound default graph dispatches straight into planning.

## Chat graph -- conversational routing

Free-form user text and slash commands both enter the chat graph. A pure-string parser handles slash commands; free-form text is routed by a small/fast LLM classifier.

```
user message
     |
     v
  [parse]
     |-- /plan <task> -----------> [planner_node]    --> END
     |                              (invoke planner/plan, return result)
     |
     |-- /<agent>:<cmd> <task> --> [specialist_node] --> END
     |                              (invoke <agent>/<cmd>)
     |
     |-- /<unknown> -------------> [respond_node]    --> END
     |                              (inline error, no LLM)
     |
     +-- free-form text --------> [triage]
                                    |
                                    |-- chat ------> [respond_node]    --> END
                                    |                 (direct LLM reply)
                                    |
                                    |-- planner ---> [planner_node]    --> END
                                    |
                                    +-- specialist-> [specialist_node] --> END
```

`respond_node` makes a direct LangChain `init_chat_model` call — no `invoke_agent`, no dependency on any registered agent. `triage_node` uses structured output (`ChatTriageResult` via `with_structured_output`) against the small/fast model configured in `MONET_CHAT_TRIAGE_MODEL`. Clarification-needed triage results stay in chat with an inline follow-up question rather than escalating with unclear intent.

Swap the whole chat graph with a different implementation (e.g. an agentic variant that delegates response generation to a `conversationalist` agent) via `MONET_CHAT_GRAPH=<module.path>:<factory>` or `[chat] graph = ...` in `monet.toml`. The client and TUI are graph-agnostic — they never import types from `orchestration/chat`. The minimal contract is: accept `{"messages": [{"role", "content"}]}` input, emit state patches with a `messages` field, optionally call `interrupt()` for HITL. See [Replacing the chat graph](../guides/custom-graphs.md#replacing-the-chat-graph) for the full contract.

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

The orchestrator also injects an `agent_roster` context entry with the current fleet-wide capability set (sourced from `CapabilityIndex`, populated by worker heartbeats) so the planner can compose across pools in split-fleet deployments without needing to speak HTTP itself. Full rationale + contract in [ADR-004](adr-004-orchestrator-fed-planner-roster.md).

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
|-- write: hansei record to kaizen log (artifact store artifact)
+-- return: final message to user
```

The hansei record accumulates across runs. Over time, patterns inform improvements to agent prompts, skill files, quality criteria, and SLA characteristics.
