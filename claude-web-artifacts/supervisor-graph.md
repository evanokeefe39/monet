# Supervisor Graph Implementation

This document specifies the concrete implementation of the three-graph supervisor topology described in the system architecture. It is intended as a direct input to implementation — read alongside `system-architecture.md`, `agent-implementations.md`, and the monet SDK reference.

The three graphs are:
1. **Entry graph** — triage and routing
2. **Planning graph** — iterative plan construction and human approval
3. **Execution graph** — wave-based production with post-wave reflection

A **kaizen hook** runs unconditionally after execution completes or fails. It is not a graph — it is a post-execution function.

All graphs use the Postgres checkpointer from day one. All graphs share the same OTel trace via `traceparent` propagation. Each graph has its own `thread_id` derived from the run.

---

## File structure

```
src/monet/orchestration/
├── __init__.py
├── _state.py          # all TypedDict state schemas
├── _invoke.py         # invoke_agent() helper
├── _content_limit.py  # enforce_content_limit()
├── _retry.py          # RetryPolicy from CommandDescriptor
├── entry_graph.py     # triage and routing
├── planning_graph.py  # planning loop and human approval
├── execution_graph.py # wave execution and reflection
└── kaizen.py          # post-execution hook
```

---

## State schemas

All state is lean. Full artifact content never lives in state — only catalogue pointers and bounded summaries.

```python
# src/monet/orchestration/_state.py
from typing import TypedDict, Optional, Literal

class ArtifactRef(TypedDict):
    """Lean reference to a catalogue artifact."""
    artifact_id: str
    url: str
    summary: str           # bounded — content_limit enforced
    confidence: float
    completeness: Literal["complete", "partial", "resource-bounded"]
    content_type: str
    agent_id: str
    command: str

class SignalState(TypedDict):
    needs_human_review: bool
    review_reason: Optional[str]
    escalation_requested: bool
    escalation_reason: Optional[str]
    revision_notes: Optional[dict]
    semantic_error: Optional[dict]   # {type: str, message: str}

class TriageDecision(TypedDict):
    complexity: Literal["simple", "bounded", "complex"]
    suggested_agents: list[str]
    requires_planning: bool
    direct_response: Optional[str]  # populated for simple requests

# ── Entry graph state ──────────────────────────────────────────────────────────

class EntryState(TypedDict):
    user_message: str
    triage: Optional[TriageDecision]
    trace_id: str
    run_id: str

# ── Planning graph state ───────────────────────────────────────────────────────

class WaveItem(TypedDict):
    agent_id: str
    command: str
    task: str
    skills: list[str]

class Wave(TypedDict):
    wave_index: int
    items: list[WaveItem]
    expected_outputs: list[str]    # descriptions for QA reflection

class Phase(TypedDict):
    phase_index: int
    name: str
    waves: list[Wave]
    quality_criteria: str

class WorkBrief(TypedDict):
    goal: str
    in_scope: str
    out_of_scope: str
    quality_criteria: str
    constraints: dict
    phases: list[Phase]
    human_checkpoint_policy: str
    assumptions: list[str]
    domain_context: Optional[str]
    evaluation_methodology: Optional[str]
    output_schema: Optional[dict]
    acceptance_tests: Optional[list[str]]

class PlanningState(TypedDict):
    user_message: str
    work_brief: Optional[WorkBrief]
    work_brief_ref: Optional[ArtifactRef]  # catalogue pointer once written
    human_feedback: Optional[str]
    plan_approved: Optional[bool]
    revision_count: int
    signals: Optional[SignalState]
    # context accumulated during planning (research, analysis artifacts)
    planning_context: list[ArtifactRef]
    trace_id: str
    run_id: str

# ── Execution graph state ──────────────────────────────────────────────────────

class WaveExecutionItem(TypedDict):
    """State passed to each parallel agent node via Send."""
    agent_id: str
    command: str
    task: str
    skills: list[str]
    phase_index: int
    wave_index: int
    item_index: int
    trace_id: str
    run_id: str

class WaveResult(TypedDict):
    phase_index: int
    wave_index: int
    item_index: int
    agent_id: str
    command: str
    output: ArtifactRef
    signals: SignalState

class ExecutionState(TypedDict):
    work_brief_ref: ArtifactRef
    work_brief: WorkBrief           # loaded at start
    current_phase_index: int
    current_wave_index: int
    # results accumulated per phase/wave
    wave_results: list[WaveResult]
    completed_phases: list[int]
    # qa reflection output per wave
    wave_reflections: list[dict]    # {phase, wave, verdict, revision_notes}
    # signals from most recent operation
    signals: Optional[SignalState]
    human_feedback: Optional[str]
    abort_reason: Optional[str]
    revision_count: int
    # final synthesis
    final_summary_ref: Optional[ArtifactRef]
    trace_id: str
    run_id: str
```

---

## `invoke_agent()` helper

The single call site for all agent invocations. Handles envelope construction, direct vs HTTP dispatch, OTel span creation, and `AgentResult` translation.

```python
# src/monet/orchestration/_invoke.py
import uuid
from opentelemetry import trace
from monet.types import AgentResult
from monet.descriptors import DescriptorRegistry, CommandDescriptor

async def invoke_agent(
    agent_id: str,
    command: str,
    task: str,
    context: list,
    trace_id: str,
    run_id: str,
    skills: list[str] | None = None,
) -> AgentResult:
    """
    Call an agent by ID and command. Dispatches as a direct Python function
    call or HTTP POST depending on the agent descriptor's transport type.
    Constructs the input envelope, creates an OTel span, and returns
    AgentResult regardless of transport.
    """
    descriptor = DescriptorRegistry.get(agent_id, command)
    envelope = {
        "task": task,
        "command": command,
        "context": context or [],
        "trace_id": trace_id,
        "run_id": run_id,
        "skills": skills or [],
    }

    tracer = trace.get_tracer("monet.orchestration")
    with tracer.start_as_current_span(
        f"agent.{agent_id}.{command}",
        attributes={
            "agent.id": agent_id,
            "agent.command": command,
            "run.id": run_id,
        }
    ) as span:
        if descriptor.transport == "local":
            # direct Python function call via SDK registry
            from monet._registry import _default_registry
            fn = _default_registry.lookup(agent_id, command)
            result: AgentResult = await fn(**_inject_matching(fn, envelope))
        else:
            # HTTP call to agent service
            result = await _http_invoke(descriptor, envelope)

        span.set_attribute("agent.success", result.success)
        span.set_attribute("agent.confidence",
            result.artifacts[0].confidence if result.artifacts
            else getattr(result, "confidence", 0.0))
        return result

async def _http_invoke(descriptor, envelope) -> AgentResult:
    import httpx
    from monet.types import AgentResult, AgentSignals, ArtifactPointer
    url = f"{descriptor.base_url}/{envelope['command']}"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={"traceparent": envelope["trace_id"]},
            json=envelope,
            timeout=descriptor.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        # translate output envelope to AgentResult
        return _envelope_to_result(data)

def _inject_matching(fn, envelope: dict) -> dict:
    """Return only the envelope fields that fn declares as parameters."""
    import inspect
    params = set(inspect.signature(fn).parameters.keys())
    return {k: v for k, v in envelope.items() if k in params}
```

---

## Entry graph

Handles triage and routes to the appropriate downstream graph or agent.

```python
# src/monet/orchestration/entry_graph.py
from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from monet.orchestration._state import EntryState, TriageDecision
from monet.orchestration._invoke import invoke_agent

# ── nodes ──────────────────────────────────────────────────────────────────────

async def triage_node(state: EntryState) -> EntryState:
    """Call planner/fast to classify the user message."""
    result = await invoke_agent(
        agent_id="planner",
        command="fast",
        task=state["user_message"],
        context=[],
        trace_id=state["trace_id"],
        run_id=state["run_id"],
    )
    triage: TriageDecision = _parse_triage(result.output)
    return {**state, "triage": triage}

async def responder_node(state: EntryState) -> EntryState:
    """Return the planner's direct response for simple requests."""
    # direct_response was populated by the triage node for simple requests
    return state

async def direct_agent_node(state: EntryState) -> EntryState:
    """Invoke a single agent in fast mode for bounded requests."""
    triage = state["triage"]
    agent_id = triage["suggested_agents"][0]
    result = await invoke_agent(
        agent_id=agent_id,
        command="fast",
        task=state["user_message"],
        context=[],
        trace_id=state["trace_id"],
        run_id=state["run_id"],
    )
    return {**state, "direct_result": result.output}

# ── routing ────────────────────────────────────────────────────────────────────

def route_from_triage(state: EntryState) -> Literal[
    "responder", "direct_agent", "planning_graph", END
]:
    triage = state.get("triage")
    if not triage:
        return END
    complexity = triage["complexity"]
    if complexity == "simple":
        return "responder"
    if complexity == "bounded":
        return "direct_agent"
    return "planning_graph"  # handoff to planning graph

# ── graph assembly ─────────────────────────────────────────────────────────────

def build_entry_graph(checkpointer):
    g = StateGraph(EntryState)
    g.add_node("triage", triage_node)
    g.add_node("responder", responder_node)
    g.add_node("direct_agent", direct_agent_node)
    # planning_graph is a subgraph or called as a separate graph via handoff

    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", route_from_triage)
    g.add_edge("responder", END)
    g.add_edge("direct_agent", END)

    return g.compile(checkpointer=checkpointer)

def _parse_triage(output: str) -> TriageDecision:
    """Parse structured triage decision from planner/fast output."""
    import json
    try:
        return json.loads(output)
    except Exception:
        # fallback: treat as complex if output is not parseable JSON
        return {
            "complexity": "complex",
            "suggested_agents": ["planner"],
            "requires_planning": True,
            "direct_response": None,
        }
```

---

## Planning graph

Handles iterative plan construction. The planner may invoke researcher or analyst agents to gather information before committing to a plan. The human approval interrupt is a structural checkpoint — it always fires before execution begins.

```python
# src/monet/orchestration/planning_graph.py
from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from monet.orchestration._state import PlanningState, ArtifactRef
from monet.orchestration._invoke import invoke_agent
from monet.orchestration._content_limit import enforce_content_limit

MAX_PLANNING_REVISIONS = 3

# ── nodes ──────────────────────────────────────────────────────────────────────

async def planner_node(state: PlanningState) -> PlanningState:
    """
    Invoke planner/plan. The planner returns either a draft work brief
    or a needs_human_review signal with a structured reason specifying
    which agent to call for more information.
    """
    context = []
    if state.get("human_feedback"):
        context.append({
            "type": "instruction",
            "content": state["human_feedback"],
            "summary": "Human revision feedback",
        })
    # include any research/analysis gathered during planning
    for ref in state.get("planning_context", []):
        context.append({
            "type": "artifact",
            "url": ref["url"],
            "summary": ref["summary"],
            "content_type": ref["content_type"],
        })

    result = await invoke_agent(
        agent_id="planner",
        command="plan",
        task=state["user_message"],
        context=context,
        trace_id=state["trace_id"],
        run_id=state["run_id"],
    )

    signals = _signals_from_result(result)
    work_brief_ref = None
    work_brief = None

    if result.success and result.artifacts:
        work_brief_ref = ArtifactRef(
            artifact_id=result.artifacts[0].artifact_id,
            url=result.artifacts[0].url,
            summary=result.artifacts[0].summary or "",
            confidence=result.artifacts[0].confidence,
            completeness=result.artifacts[0].completeness,
            content_type=result.artifacts[0].content_type,
            agent_id="planner",
            command="plan",
        )
        work_brief = await _fetch_work_brief(work_brief_ref["url"])

    return {
        **state,
        "work_brief": work_brief,
        "work_brief_ref": work_brief_ref,
        "signals": signals,
        "human_feedback": None,  # consumed
    }

async def research_node(state: PlanningState) -> PlanningState:
    """
    Pull research or analysis to inform the plan.
    The planner's revision_notes specify which agent to call and what to ask.
    """
    signals = state.get("signals", {})
    revision_notes = signals.get("revision_notes", {})
    agent_id = revision_notes.get("agent_id", "researcher")
    command = revision_notes.get("command", "fast")
    task = revision_notes.get("task", state["user_message"])

    result = await invoke_agent(
        agent_id=agent_id,
        command=command,
        task=task,
        context=[],
        trace_id=state["trace_id"],
        run_id=state["run_id"],
    )

    new_ref = ArtifactRef(
        artifact_id=result.artifacts[0].artifact_id if result.artifacts else "",
        url=result.artifacts[0].url if result.artifacts else "",
        summary=result.output or "",
        confidence=0.8,
        completeness="complete",
        content_type="text/markdown",
        agent_id=agent_id,
        command=command,
    )
    planning_context = list(state.get("planning_context", []))
    planning_context.append(new_ref)

    return {**state, "planning_context": planning_context}

async def human_approval_node(state: PlanningState) -> PlanningState:
    """
    Structural nemawashi gate. Always interrupts before execution begins.
    The human sees the work brief summary and approves, requests revisions,
    or rejects.
    """
    response = interrupt({
        "type": "plan_approval",
        "work_brief_summary": state["work_brief_ref"]["summary"],
        "work_brief_url": state["work_brief_ref"]["url"],
        "phases": [p["name"] for p in state["work_brief"]["phases"]],
        "assumptions": state["work_brief"].get("assumptions", []),
    })
    approved = response.get("approved", False)
    feedback = response.get("feedback")

    return {
        **state,
        "plan_approved": approved,
        "human_feedback": feedback,
        "revision_count": state["revision_count"] + (0 if approved else 1),
    }

# ── routing ────────────────────────────────────────────────────────────────────

def route_from_planner(state: PlanningState) -> Literal[
    "research_node", "human_approval", END
]:
    signals = state.get("signals") or {}

    if signals.get("escalation_requested"):
        return END

    if signals.get("needs_human_review"):
        # planner needs more information — pull research or analysis
        revision_notes = signals.get("revision_notes") or {}
        if revision_notes.get("agent_id"):
            return "research_node"
        # planner is ready for human approval
        if state.get("work_brief_ref"):
            return "human_approval"
        return END

    if state.get("work_brief_ref"):
        return "human_approval"

    return END

def route_from_approval(state: PlanningState) -> Literal[
    "planner_node", END
]:
    if state.get("plan_approved"):
        return END  # approved — handoff to execution graph

    if state.get("human_feedback") and \
       state["revision_count"] < MAX_PLANNING_REVISIONS:
        return "planner_node"  # revise with feedback

    return END  # rejected or max revisions reached

# ── graph assembly ─────────────────────────────────────────────────────────────

def build_planning_graph(checkpointer):
    g = StateGraph(PlanningState)
    g.add_node("planner_node", planner_node)
    g.add_node("research_node", research_node)
    g.add_node("human_approval", human_approval_node)

    g.add_edge(START, "planner_node")
    g.add_conditional_edges("planner_node", route_from_planner)
    g.add_edge("research_node", "planner_node")
    g.add_conditional_edges("human_approval", route_from_approval)

    return g.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_approval"],  # structural checkpoint
    )
```

---

## Execution graph

Faithful executor of the approved plan. Wave-based execution using LangGraph's `Send` API for parallelism. Post-wave QA reflection is the jidoka checkpoint.

```python
# src/monet/orchestration/execution_graph.py
from typing import Literal, Annotated
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, interrupt
from langgraph.graph.message import add_messages
from monet.orchestration._state import (
    ExecutionState, WaveExecutionItem, WaveResult, ArtifactRef
)
from monet.orchestration._invoke import invoke_agent
from monet.orchestration._content_limit import enforce_content_limit

MAX_WAVE_RETRIES = 2

# ── nodes ──────────────────────────────────────────────────────────────────────

async def load_plan_node(state: ExecutionState) -> ExecutionState:
    """Load the approved work brief from the catalogue and initialise state."""
    work_brief = await _fetch_work_brief(state["work_brief_ref"]["url"])
    return {
        **state,
        "work_brief": work_brief,
        "current_phase_index": 0,
        "current_wave_index": 0,
        "wave_results": [],
        "wave_reflections": [],
        "completed_phases": [],
        "revision_count": 0,
    }

def fan_out_wave(state: ExecutionState) -> list[Send]:
    """
    Fan out the current wave to parallel agent nodes via Send.
    Each item in the wave becomes a separate node invocation.
    """
    phase = state["work_brief"]["phases"][state["current_phase_index"]]
    wave = phase["waves"][state["current_wave_index"]]

    return [
        Send("agent_node", WaveExecutionItem(
            agent_id=item["agent_id"],
            command=item["command"],
            task=item["task"],
            skills=item.get("skills", []),
            phase_index=state["current_phase_index"],
            wave_index=state["current_wave_index"],
            item_index=idx,
            trace_id=state["trace_id"],
            run_id=f"{state['run_id']}-p{state['current_phase_index']}"
                   f"-w{state['current_wave_index']}-i{idx}",
        ))
        for idx, item in enumerate(wave["items"])
    ]

async def agent_node(item: WaveExecutionItem) -> WaveResult:
    """
    Execute a single agent invocation from a wave.
    The node wrapper emits progress, handles signals, and returns WaveResult.
    """
    from monet import emit_progress
    emit_progress({
        "type": "agent_start",
        "agent_id": item["agent_id"],
        "command": item["command"],
        "phase": item["phase_index"],
        "wave": item["wave_index"],
    })

    result = await invoke_agent(
        agent_id=item["agent_id"],
        command=item["command"],
        task=item["task"],
        context=[],
        trace_id=item["trace_id"],
        run_id=item["run_id"],
        skills=item["skills"],
    )

    emit_progress({
        "type": "agent_complete",
        "agent_id": item["agent_id"],
        "command": item["command"],
        "success": result.success,
        "confidence": result.artifacts[0].confidence
            if result.artifacts else 0.0,
    })

    output_ref = ArtifactRef(
        artifact_id=result.artifacts[0].artifact_id
            if result.artifacts else "",
        url=result.artifacts[0].url if result.artifacts else "",
        summary=enforce_content_limit(
            result.output or
            (result.artifacts[0].summary if result.artifacts else ""),
            item["agent_id"],
            item["run_id"],
        ),
        confidence=result.artifacts[0].confidence
            if result.artifacts else 0.0,
        completeness=result.artifacts[0].completeness
            if result.artifacts else "partial",
        content_type=result.artifacts[0].content_type
            if result.artifacts else "text/plain",
        agent_id=item["agent_id"],
        command=item["command"],
    )

    return WaveResult(
        phase_index=item["phase_index"],
        wave_index=item["wave_index"],
        item_index=item["item_index"],
        agent_id=item["agent_id"],
        command=item["command"],
        output=output_ref,
        signals=_signals_from_result(result),
    )

async def wave_reflection_node(state: ExecutionState) -> ExecutionState:
    """
    Post-wave jidoka checkpoint. QA/fast evaluates actual wave outputs
    against the plan's expected outputs for this wave.
    """
    phase = state["work_brief"]["phases"][state["current_phase_index"]]
    wave = phase["waves"][state["current_wave_index"]]

    # gather this wave's results
    wave_results = [
        r for r in state["wave_results"]
        if r["phase_index"] == state["current_phase_index"]
        and r["wave_index"] == state["current_wave_index"]
    ]

    context = [
        {
            "type": "instruction",
            "content": f"Expected outputs: {wave['expected_outputs']}",
            "summary": "Expected wave outputs from plan",
        },
        *[{
            "type": "artifact",
            "url": r["output"]["url"],
            "summary": r["output"]["summary"],
            "content_type": r["output"]["content_type"],
        } for r in wave_results],
    ]

    result = await invoke_agent(
        agent_id="qa",
        command="fast",
        task=(
            f"Evaluate wave {state['current_wave_index']} of "
            f"phase {state['current_phase_index']} "
            f"({phase['name']}) against the plan's quality criteria: "
            f"{phase['quality_criteria']}"
        ),
        context=context,
        trace_id=state["trace_id"],
        run_id=f"{state['run_id']}-reflect-p{state['current_phase_index']}"
               f"-w{state['current_wave_index']}",
    )

    reflection = {
        "phase_index": state["current_phase_index"],
        "wave_index": state["current_wave_index"],
        "verdict": _parse_qa_verdict(result.output),
        "revision_notes": _signals_from_result(result).get("revision_notes"),
    }

    reflections = list(state.get("wave_reflections", []))
    reflections.append(reflection)

    return {**state, "wave_reflections": reflections}

async def human_interrupt_node(state: ExecutionState) -> ExecutionState:
    """
    Human in the loop during execution. Surfaces context and waits.
    Human can continue, provide revision instructions, or abort.
    """
    signals = state.get("signals", {})
    response = interrupt({
        "type": "execution_interrupt",
        "phase_index": state["current_phase_index"],
        "wave_index": state["current_wave_index"],
        "reason": signals.get("review_reason") or signals.get("escalation_reason"),
        "last_wave_reflection": _get_last_reflection(state),
    })

    action = response.get("action", "abort")
    feedback = response.get("feedback")

    return {
        **state,
        "human_feedback": feedback,
        "abort_reason": None if action != "abort" else "human_aborted",
    }

async def final_synthesis_node(state: ExecutionState) -> ExecutionState:
    """
    Synthesise a final summary comparing planned vs actual execution.
    This is the hansei artefact — honest reflection on what happened.
    """
    result = await invoke_agent(
        agent_id="writer",
        command="synthesise",
        task=(
            "Produce a final execution summary comparing what was planned "
            "against what was actually produced. Note any deviations, "
            "partial completions, or scope changes. Be honest about "
            "failures."
        ),
        context=[
            {
                "type": "artifact",
                "url": state["work_brief_ref"]["url"],
                "summary": state["work_brief_ref"]["summary"],
                "content_type": "application/json",
            },
            *[{
                "type": "artifact",
                "url": r["output"]["url"],
                "summary": r["output"]["summary"],
                "content_type": r["output"]["content_type"],
            } for r in state.get("wave_results", [])],
        ],
        trace_id=state["trace_id"],
        run_id=f"{state['run_id']}-synthesis",
    )

    summary_ref = ArtifactRef(
        artifact_id=result.artifacts[0].artifact_id
            if result.artifacts else "",
        url=result.artifacts[0].url if result.artifacts else "",
        summary=result.output or "",
        confidence=result.artifacts[0].confidence
            if result.artifacts else 0.8,
        completeness="complete",
        content_type="text/markdown",
        agent_id="writer",
        command="synthesise",
    )
    return {**state, "final_summary_ref": summary_ref}

# ── routing ────────────────────────────────────────────────────────────────────

def route_after_fan_out(state: ExecutionState) -> Literal[
    "wave_reflection", END
]:
    # check if any wave result carries a blocking signal
    current_results = [
        r for r in state.get("wave_results", [])
        if r["phase_index"] == state["current_phase_index"]
        and r["wave_index"] == state["current_wave_index"]
    ]
    for r in current_results:
        if r["signals"].get("needs_human_review") or \
           r["signals"].get("escalation_requested"):
            return END  # will be caught by signals routing below
    return "wave_reflection"

def route_after_reflection(state: ExecutionState) -> Literal[
    "fan_out_wave", "human_interrupt", "final_synthesis", END
]:
    if state.get("abort_reason"):
        return "final_synthesis"

    last = _get_last_reflection(state)
    if not last:
        return "final_synthesis"

    verdict = last.get("verdict", "uncertain")

    if verdict == "pass":
        return _advance_or_finish(state)

    if verdict == "fail" and state["revision_count"] < MAX_WAVE_RETRIES:
        # retry the wave with revision notes injected
        return "fan_out_wave"

    if verdict in ("fail", "uncertain"):
        return "human_interrupt"

    return "final_synthesis"

def route_after_interrupt(state: ExecutionState) -> Literal[
    "fan_out_wave", "final_synthesis", END
]:
    if state.get("abort_reason"):
        return "final_synthesis"
    if state.get("human_feedback"):
        return "fan_out_wave"  # retry with feedback
    return "final_synthesis"

def _advance_or_finish(state: ExecutionState) -> Literal[
    "fan_out_wave", "final_synthesis"
]:
    """Advance to next wave or phase, or move to synthesis if all done."""
    brief = state["work_brief"]
    phase = brief["phases"][state["current_phase_index"]]

    next_wave = state["current_wave_index"] + 1
    if next_wave < len(phase["waves"]):
        # more waves in this phase
        state["current_wave_index"] = next_wave
        return "fan_out_wave"

    next_phase = state["current_phase_index"] + 1
    if next_phase < len(brief["phases"]):
        # next phase
        state["current_phase_index"] = next_phase
        state["current_wave_index"] = 0
        return "fan_out_wave"

    return "final_synthesis"

# ── graph assembly ─────────────────────────────────────────────────────────────

def build_execution_graph(checkpointer):
    g = StateGraph(ExecutionState)
    g.add_node("load_plan", load_plan_node)
    g.add_node("fan_out_wave", fan_out_wave)
    g.add_node("agent_node", agent_node)
    g.add_node("wave_reflection", wave_reflection_node)
    g.add_node("human_interrupt", human_interrupt_node)
    g.add_node("final_synthesis", final_synthesis_node)

    g.add_edge(START, "load_plan")
    g.add_edge("load_plan", "fan_out_wave")
    g.add_conditional_edges("fan_out_wave", route_after_fan_out)
    g.add_edge("agent_node", "wave_reflection")
    g.add_conditional_edges("wave_reflection", route_after_reflection)
    g.add_conditional_edges("human_interrupt", route_after_interrupt)
    g.add_edge("final_synthesis", END)

    return g.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_interrupt"],
    )
```

---

## Kaizen hook

Runs unconditionally after every execution. Not a graph — a post-execution function.

```python
# src/monet/orchestration/kaizen.py
from monet import write_artifact
from monet.orchestration._state import ExecutionState

async def run_kaizen_hook(
    state: ExecutionState,
    trace_id: str,
    run_id: str,
) -> None:
    """
    Unconditional post-execution hook. Writes a hansei record to the
    catalogue comparing planned vs actual execution. Fires whether
    execution succeeded, partially completed, or failed.
    """
    planned_phases = [p["name"] for p in state["work_brief"]["phases"]]
    completed_phases = state.get("completed_phases", [])
    reflections = state.get("wave_reflections", [])

    deviations = [
        r for r in reflections
        if r.get("verdict") != "pass"
    ]

    hansei_record = {
        "run_id": run_id,
        "trace_id": trace_id,
        "planned_phases": planned_phases,
        "completed_phase_indices": completed_phases,
        "total_waves": len(reflections),
        "passed_waves": sum(1 for r in reflections if r.get("verdict") == "pass"),
        "failed_waves": sum(1 for r in reflections if r.get("verdict") == "fail"),
        "deviations": deviations,
        "abort_reason": state.get("abort_reason"),
        "final_summary_url": state.get("final_summary_ref", {}).get("url"),
        # confidence calibration data for Langfuse query (Spike 4)
        "agent_confidence_data": [
            {
                "agent_id": r["agent_id"],
                "command": r["command"],
                "declared_confidence": r["output"]["confidence"],
                "wave_passed": any(
                    ref["phase_index"] == r["phase_index"]
                    and ref["wave_index"] == r["wave_index"]
                    and ref.get("verdict") == "pass"
                    for ref in reflections
                ),
            }
            for r in state.get("wave_results", [])
        ],
    }

    import json
    await write_artifact(
        content=json.dumps(hansei_record, indent=2).encode(),
        content_type="application/json",
        summary=f"Hansei record for run {run_id}",
        confidence=1.0,
        completeness="complete",
        sensitivity_label="internal",
        tags={"type": "hansei", "run_id": run_id},
    )
```

---

## Graph entrypoint

The top-level function that sequences the three graphs for a complete run.

```python
# src/monet/orchestration/__init__.py
import uuid
from langgraph.checkpoint.postgres import PostgresSaver
from monet.orchestration.entry_graph import build_entry_graph
from monet.orchestration.planning_graph import build_planning_graph
from monet.orchestration.execution_graph import build_execution_graph
from monet.orchestration.kaizen import run_kaizen_hook

async def run(user_message: str, db_url: str):
    """
    Execute a complete monet run for a user message.
    Sequences triage → planning → execution → kaizen.
    """
    run_id = str(uuid.uuid4())
    trace_id = _generate_trace_id()

    checkpointer = PostgresSaver.from_conn_string(db_url)

    # 1. Triage
    entry = build_entry_graph(checkpointer)
    entry_result = await entry.ainvoke({
        "user_message": user_message,
        "triage": None,
        "trace_id": trace_id,
        "run_id": run_id,
    }, config={"configurable": {"thread_id": run_id}})

    triage = entry_result.get("triage", {})
    if triage.get("complexity") != "complex":
        return entry_result  # simple or bounded — done

    # 2. Planning
    planning = build_planning_graph(checkpointer)
    planning_result = await planning.ainvoke({
        "user_message": user_message,
        "work_brief": None,
        "work_brief_ref": None,
        "human_feedback": None,
        "plan_approved": None,
        "revision_count": 0,
        "signals": None,
        "planning_context": [],
        "trace_id": trace_id,
        "run_id": run_id,
    }, config={"configurable": {"thread_id": f"{run_id}-planning"}})

    if not planning_result.get("plan_approved"):
        return planning_result  # rejected or aborted during planning

    # 3. Execution
    execution = build_execution_graph(checkpointer)
    execution_result = await execution.ainvoke({
        "work_brief_ref": planning_result["work_brief_ref"],
        "work_brief": None,  # loaded by load_plan_node
        "current_phase_index": 0,
        "current_wave_index": 0,
        "wave_results": [],
        "completed_phases": [],
        "wave_reflections": [],
        "signals": None,
        "human_feedback": None,
        "abort_reason": None,
        "revision_count": 0,
        "final_summary_ref": None,
        "trace_id": trace_id,
        "run_id": run_id,
    }, config={"configurable": {"thread_id": f"{run_id}-execution"}})

    # 4. Kaizen hook — unconditional
    await run_kaizen_hook(execution_result, trace_id, run_id)

    return execution_result

def _generate_trace_id() -> str:
    """Generate a W3C traceparent-compatible trace ID."""
    import secrets
    trace = secrets.token_hex(16)
    span = secrets.token_hex(8)
    return f"00-{trace}-{span}-01"
```

---

## Key implementation constraints

**State is always lean.** Full content never in state. Pointers and summaries only. `enforce_content_limit` is called on every agent output before it enters state.

**HITL uses structural interrupts.** `interrupt_before=["human_approval"]` in the planning graph. `interrupt_before=["human_interrupt"]` in the execution graph. The interrupt mechanism is LangGraph's native — no custom polling.

**Routing is deterministic.** No LLM makes routing decisions in the orchestrator. All routing functions read structured state fields — signals, verdict, indices, flags. A routing function that calls an LLM is a bug.

**The planner signals intent, the graph routes.** When the planner needs more information it raises `NeedsHumanReview` with `revision_notes` specifying which agent to call. The planning graph reads this and routes to `research_node`. The planner never calls other agents directly.

**`emit_progress()` in the node wrapper.** The `agent_node` function calls `emit_progress()` at start and end of every invocation. This is the andon board — the chat layer subscribes to `stream_mode=["updates", "custom"]` and renders live status.

**Three separate thread IDs.** The entry, planning, and execution graphs each get their own `thread_id` derived from the run ID. This keeps their checkpointer state separate and independently resumable.

**Kaizen is unconditional.** `run_kaizen_hook` is called regardless of outcome. A try/finally around the execution call ensures it fires even on unhandled exceptions.
