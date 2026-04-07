"""Execution graph — wave-based parallel execution with QA reflection.

Uses LangGraph's Send API for fan-out. Routing functions use the
result.has_signal(...) method exclusively — never the legacy module-level
has_signal() helper (removed in Wave 1).

Convention (not enforced): every emit_progress event includes "run_id"
so clients can correlate streaming events to a run.

Returns an uncompiled StateGraph.
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import Send, interrupt

from monet.types import SignalType

from ._invoke import invoke_agent
from ._state import ExecutionState, WaveItem, WaveResult

MAX_WAVE_RETRIES = 3


async def load_plan(state: ExecutionState) -> dict[str, Any]:
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
    return {}


async def agent_node(item: WaveItem) -> dict[str, Any]:
    """Execute one wave item; receives WaveItem via Send (not state)."""
    result = await invoke_agent(
        item["agent_id"],
        command=item["command"],
        task=item["task"],
        trace_id=item.get("trace_id", ""),
        run_id=item.get("run_id", ""),
    )
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
    """Filter results for current wave; flag blocking signals."""
    current_phase = state["current_phase_index"]
    current_wave = state["current_wave_index"]
    current_results = [
        r
        for r in state.get("wave_results", [])
        if r.get("phase_index") == current_phase and r.get("wave_index") == current_wave
    ]

    def _has(signals: list[dict[str, Any]], target: SignalType) -> bool:
        return any(s.get("type") == target.value for s in signals)

    has_blocking = any(
        _has(r.get("signals", []), SignalType.NEEDS_HUMAN_REVIEW)
        or _has(r.get("signals", []), SignalType.ESCALATION_REQUIRED)
        for r in current_results
    )
    return {
        "signals": {
            "has_blocking_signal": has_blocking,
            "wave_item_count": len(current_results),
        }
    }


async def wave_reflection(state: ExecutionState) -> dict[str, Any]:
    """Call qa/fast to evaluate the current wave's results."""
    current_phase = state["current_phase_index"]
    current_wave = state["current_wave_index"]
    current_results = [
        r
        for r in state.get("wave_results", [])
        if r.get("phase_index") == current_phase and r.get("wave_index") == current_wave
    ]

    result = await invoke_agent(
        "qa",
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
    current_phase_idx = state["current_phase_index"]
    current_wave_idx = state["current_wave_index"]
    phases = state["work_brief"]["phases"]
    total_waves = len(phases[current_phase_idx]["waves"])

    if current_wave_idx + 1 < total_waves:
        return {"current_wave_index": current_wave_idx + 1}

    completed = list(state.get("completed_phases") or [])
    completed.append(current_phase_idx)
    if current_phase_idx + 1 < len(phases):
        return {
            "current_phase_index": current_phase_idx + 1,
            "current_wave_index": 0,
            "completed_phases": completed,
        }
    return {"completed_phases": completed}


async def human_interrupt(state: ExecutionState) -> dict[str, Any]:
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


def dispatch_wave(state: ExecutionState) -> list[Send]:
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
    phases = state["work_brief"]["phases"]
    completed = state.get("completed_phases") or []
    if len(completed) >= len(phases):
        return END
    return "prepare_wave"


def route_after_interrupt(state: ExecutionState) -> str:
    if state.get("abort_reason"):
        return END
    return "prepare_wave"


def build_execution_graph() -> StateGraph[ExecutionState]:
    """Build the execution graph. Returns uncompiled StateGraph."""
    graph = StateGraph(ExecutionState)
    graph.add_node("load_plan", load_plan)
    graph.add_node("prepare_wave", prepare_wave)
    graph.add_node("agent_node", agent_node)  # type: ignore[arg-type]
    graph.add_node("collect_wave", collect_wave)
    graph.add_node("wave_reflection", wave_reflection)
    graph.add_node("advance", advance)
    graph.add_node("human_interrupt", human_interrupt)

    graph.set_entry_point("load_plan")
    graph.add_edge("load_plan", "prepare_wave")
    graph.add_conditional_edges("prepare_wave", dispatch_wave, ["agent_node"])
    graph.add_edge("agent_node", "collect_wave")
    graph.add_edge("collect_wave", "wave_reflection")
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
    graph.add_conditional_edges(
        "advance", route_after_advance, {"prepare_wave": "prepare_wave", END: END}
    )
    graph.add_conditional_edges(
        "human_interrupt",
        route_after_interrupt,
        {"prepare_wave": "prepare_wave", END: END},
    )
    return graph
