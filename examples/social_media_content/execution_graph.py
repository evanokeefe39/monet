"""Execution graph — wave-based parallel execution with QA reflection.

Uses LangGraph's Send API for wave fan-out. Each wave item becomes a
parallel agent_node invocation. Results merge via append reducer on
wave_results. Post-wave QA reflection implements jidoka (stop on defect).

Nodes:
  load_plan — initialize execution state from work brief
  prepare_wave — passthrough convergence point before fan-out
  agent_node — execute a single wave item (receives WaveItem, not ExecutionState)
  collect_wave — join parallel results, check for blocking signals
  wave_reflection — QA evaluation of wave results
  advance — increment wave/phase counters
  human_interrupt — HITL gate on QA failure

The prepare_wave node exists so that multiple paths (load_plan, advance,
human_interrupt) can converge before the Send-based fan-out. The
dispatch_wave conditional edge is attached to prepare_wave.
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import Send, interrupt

from monet.orchestration import invoke_agent
from monet.types import SignalType

from .state import ExecutionState, WaveItem, WaveResult

MAX_WAVE_RETRIES = 3


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def load_plan(state: ExecutionState) -> dict[str, Any]:
    """Initialize execution state from the approved work brief."""
    return {
        "current_phase_index": 0,
        "current_wave_index": 0,
        "wave_results": [],
        "wave_reflections": [],
        "completed_phases": [],
        "revision_count": 0,
        "signals": None,
        "abort_reason": None,
    }


async def prepare_wave(state: ExecutionState) -> dict[str, Any]:
    """Passthrough node — convergence point before Send fan-out.

    Multiple paths route here (load_plan, advance, human_interrupt).
    The dispatch_wave conditional edge is attached to this node's output.
    """
    return {}


async def agent_node(item: WaveItem) -> dict[str, Any]:
    """Execute a single agent invocation from a wave item.

    Receives a WaveItem dict via Send (not ExecutionState).
    Returns only {"wave_results": [result_entry]}. All other
    ExecutionState fields retain their values from the previous state.
    The append reducer on wave_results accumulates results from all
    parallel Send invocations.
    """
    result = await invoke_agent(
        item["agent_id"],
        command=item["command"],
        task=item["task"],
        trace_id=item.get("trace_id", ""),
        run_id=item.get("run_id", ""),
    )

    # Convert list-based signals to serializable dict for state
    signals_data = [dict(s) for s in result.signals]

    if isinstance(result.output, str):
        output_str = result.output
    elif isinstance(result.output, dict):
        output_str = result.output.get("url", "")
    else:
        output_str = ""

    entry: WaveResult = {
        "phase_index": item["phase_index"],
        "wave_index": item["wave_index"],
        "item_index": item["item_index"],
        "agent_id": item["agent_id"],
        "command": item["command"],
        "output": output_str,
        "signals": signals_data,
    }

    return {"wave_results": [entry]}


async def collect_wave(state: ExecutionState) -> dict[str, Any]:
    """Join parallel wave results and check for blocking signals.

    Filters wave_results for entries matching the current phase_index
    and wave_index. Checks if any entry carries a blocking signal
    (needs_human_review or escalation_requested). Sets the signals
    field on state so the routing function can read it.

    Does not invoke any agent. Purely a state transformation step.
    """
    current_phase = state["current_phase_index"]
    current_wave = state["current_wave_index"]

    current_results = [
        r
        for r in state.get("wave_results", [])
        if r.get("phase_index") == current_phase and r.get("wave_index") == current_wave
    ]

    def _has(signals: list[dict], stype: SignalType) -> bool:
        return any(s.get("type") == stype.value for s in signals)

    has_blocking = any(
        _has(r.get("signals", []), SignalType.NEEDS_HUMAN_REVIEW)
        or _has(r.get("signals", []), SignalType.ESCALATION_REQUIRED)
        for r in current_results
    )

    signals = {
        "has_blocking_signal": has_blocking,
        "wave_item_count": len(current_results),
    }

    return {"signals": signals}


async def wave_reflection(state: ExecutionState) -> dict[str, Any]:
    """Call sm-qa/fast to evaluate the current wave's results."""
    current_phase = state["current_phase_index"]
    current_wave = state["current_wave_index"]

    current_results = [
        r
        for r in state.get("wave_results", [])
        if r.get("phase_index") == current_phase and r.get("wave_index") == current_wave
    ]

    result = await invoke_agent(
        "sm-qa",
        command="fast",
        task=f"Evaluate wave {current_phase}.{current_wave} results",
        context=[
            {
                "type": "artifact",
                "summary": f"Wave {current_phase}.{current_wave} results",
                "content": json.dumps(current_results),
            }
        ],
        trace_id=state.get("trace_id", ""),
        run_id=state.get("run_id", ""),
    )

    verdict_data: dict[str, Any] = {}
    if isinstance(result.output, str) and result.output.strip():
        try:
            verdict_data = json.loads(result.output)
        except json.JSONDecodeError:
            verdict_data = {"verdict": "pass", "notes": result.output[:200]}

    reflection = {
        "phase_index": current_phase,
        "wave_index": current_wave,
        "verdict": verdict_data.get("verdict", "pass"),
        "notes": verdict_data.get("notes", ""),
    }

    reflections = list(state.get("wave_reflections") or [])
    reflections.append(reflection)

    update: dict[str, Any] = {"wave_reflections": reflections}
    if reflection["verdict"] == "fail":
        update["revision_count"] = state.get("revision_count", 0) + 1
    return update


async def advance(state: ExecutionState) -> dict[str, Any]:
    """Increment wave/phase counters or signal completion.

    Does not invoke any agent.
    """
    current_phase_idx = state["current_phase_index"]
    current_wave_idx = state["current_wave_index"]
    phases = state["work_brief"]["phases"]
    current_phase = phases[current_phase_idx]
    total_waves = len(current_phase["waves"])

    if current_wave_idx + 1 < total_waves:
        return {"current_wave_index": current_wave_idx + 1}

    # Phase complete
    completed = list(state.get("completed_phases") or [])
    completed.append(current_phase_idx)

    if current_phase_idx + 1 < len(phases):
        return {
            "current_phase_index": current_phase_idx + 1,
            "current_wave_index": 0,
            "completed_phases": completed,
        }

    # All phases complete
    return {"completed_phases": completed}


async def human_interrupt(state: ExecutionState) -> dict[str, Any]:
    """HITL gate triggered by QA failure or blocking signals.

    Resume with: {"action": "continue"|"abort", "feedback": str|None}
    """
    reflections = state.get("wave_reflections") or []
    last_reflection = reflections[-1] if reflections else {}

    decision = interrupt(
        {
            "reason": "Wave QA failure or blocking signal",
            "phase_index": state["current_phase_index"],
            "wave_index": state["current_wave_index"],
            "last_reflection": last_reflection,
        }
    )

    if isinstance(decision, dict) and decision.get("action") == "abort":
        return {"abort_reason": decision.get("feedback", "Aborted by human")}

    return {}


# ---------------------------------------------------------------------------
# Routing functions — deterministic, no LLM
# ---------------------------------------------------------------------------


def dispatch_wave(state: ExecutionState) -> list[Send]:
    """Fan out the current wave to parallel agent nodes via Send.

    Attached as conditional edge from prepare_wave. Returns a
    list[Send], one per wave item.
    """
    phase = state["work_brief"]["phases"][state["current_phase_index"]]
    wave = phase["waves"][state["current_wave_index"]]

    return [
        Send(
            "agent_node",
            WaveItem(
                agent_id=item["agent_id"],
                command=item["command"],
                task=item["task"],
                phase_index=state["current_phase_index"],
                wave_index=state["current_wave_index"],
                item_index=idx,
                trace_id=state.get("trace_id", ""),
                run_id=state.get("run_id", ""),
            ),
        )
        for idx, item in enumerate(wave["items"])
    ]


def route_after_reflection(state: ExecutionState) -> str:
    """Route after QA reflection."""
    signals = state.get("signals") or {}
    reflections = state.get("wave_reflections") or []
    last_reflection = reflections[-1] if reflections else {}

    if signals.get("has_blocking_signal"):
        return "human_interrupt"

    verdict = last_reflection.get("verdict", "pass")

    if verdict == "pass":
        return "advance"

    if verdict == "fail" and state.get("revision_count", 0) < MAX_WAVE_RETRIES:
        return "prepare_wave"

    return END


def route_after_advance(state: ExecutionState) -> str:
    """Route after advancing wave/phase counters."""
    phases = state["work_brief"]["phases"]
    completed = state.get("completed_phases") or []

    if len(completed) >= len(phases):
        return END

    return "prepare_wave"


def route_after_interrupt(state: ExecutionState) -> str:
    """Route after human interrupt decision."""
    if state.get("abort_reason"):
        return END
    return "prepare_wave"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_execution_graph() -> StateGraph:
    """Build the execution graph with Send-based wave parallelism.

    Graph structure:
      load_plan -> prepare_wave -> [Send -> agent_node] -> collect_wave
      -> wave_reflection -> advance -> prepare_wave (loop)
                         -> human_interrupt -> prepare_wave (retry)
                         -> END (abort/complete)
    """
    graph = StateGraph(ExecutionState)

    graph.add_node("load_plan", load_plan)
    graph.add_node("prepare_wave", prepare_wave)
    graph.add_node("agent_node", agent_node)
    graph.add_node("collect_wave", collect_wave)
    graph.add_node("wave_reflection", wave_reflection)
    graph.add_node("advance", advance)
    graph.add_node("human_interrupt", human_interrupt)

    graph.set_entry_point("load_plan")

    # load_plan -> prepare_wave (always)
    graph.add_edge("load_plan", "prepare_wave")

    # prepare_wave -> fan out via Send to agent_node
    graph.add_conditional_edges("prepare_wave", dispatch_wave, ["agent_node"])

    # All parallel agent_nodes -> collect_wave
    graph.add_edge("agent_node", "collect_wave")

    # collect_wave -> wave_reflection
    graph.add_edge("collect_wave", "wave_reflection")

    # wave_reflection -> route based on verdict/signals
    graph.add_conditional_edges(
        "wave_reflection",
        route_after_reflection,
        {
            "advance": "advance",
            "human_interrupt": "human_interrupt",
            "prepare_wave": "prepare_wave",
            END: END,
        },
    )

    # advance -> next wave or END
    graph.add_conditional_edges(
        "advance",
        route_after_advance,
        {"prepare_wave": "prepare_wave", END: END},
    )

    # human_interrupt -> retry or END
    graph.add_conditional_edges(
        "human_interrupt",
        route_after_interrupt,
        {"prepare_wave": "prepare_wave", END: END},
    )

    return graph
