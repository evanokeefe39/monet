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

from langchain_core.runnables import (
    RunnableConfig,  # noqa: TC002 — needed at runtime for LangGraph signature introspection
)
from langgraph.graph import END, StateGraph
from langgraph.types import Send, interrupt

from monet import emit_progress, get_catalogue
from monet._registry import default_registry
from monet._tracing import (
    detach_trace_context,
    extract_and_attach_trace_context,
    get_tracer,
    inject_trace_context,
)
from monet.exceptions import SemanticError
from monet.signals import BLOCKING, in_group

from ._invoke import extract_carrier_from_config, invoke_agent
from ._state import ExecutionState, WaveItem, WaveResult
from ._validate import _assert_registered

MAX_WAVE_RETRIES = 3


def _latest_attempts(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate wave_results by ``item_index``, keeping the last.

    ``ExecutionState.wave_results`` uses an append-only reducer, so a
    wave retried after a blocking signal accumulates both the stale
    failed attempt and the fresh attempt. Any code that inspects the
    current wave's results must look at the most recent attempt per
    item only, otherwise old blocking signals re-trigger the human
    interrupt forever (and QA evaluates stale context alongside fresh
    content). Caller is expected to have already filtered by
    ``(phase_index, wave_index)``.
    """
    latest: dict[int, dict[str, Any]] = {}
    for r in results:
        idx = r.get("item_index")
        if not isinstance(idx, int):
            # Shouldn't happen in practice; preserve the entry under a
            # synthetic key so we don't silently drop results.
            idx = -id(r)
        latest[idx] = r
    return list(latest.values())


async def load_plan(state: ExecutionState, config: RunnableConfig) -> dict[str, Any]:
    # If the CLI injected a root carrier via run metadata, attach it
    # first so the "monet.execution" span we open below becomes a child
    # of that root instead of starting a fresh trace. This is what
    # unifies the triage + planning + execution graphs under one
    # Langfuse trace keyed by the CLI's monet.run span.
    upstream_carrier = extract_carrier_from_config(config)
    upstream_token = (
        extract_and_attach_trace_context(upstream_carrier) if upstream_carrier else None
    )
    try:
        tracer = get_tracer("monet.execution")
        work_brief = state.get("work_brief") or {}
        phases = work_brief.get("phases") or []
        with tracer.start_as_current_span(
            "monet.execution",
            attributes={
                "monet.run_id": state.get("run_id", ""),
                "monet.trace_id": state.get("trace_id", ""),
                "monet.phase_count": len(phases),
                "monet.goal": (work_brief.get("goal") or "")[:200],
            },
        ):
            carrier = inject_trace_context()
    finally:
        if upstream_token is not None:
            detach_trace_context(upstream_token)
    return {
        "current_phase_index": 0,
        "current_wave_index": 0,
        "wave_results": [],
        "wave_reflections": [],
        "completed_phases": [],
        "revision_count": 0,
        "signals": None,
        "abort_reason": None,
        "pending_context": [],
        "trace_carrier": carrier,
    }


async def _resolve_wave_result(wr: dict[str, Any]) -> dict[str, Any]:
    """Convert a stored WaveResult into a context entry for downstream agents.

    If the inline ``output`` is short or absent and the result has catalogue
    artifacts, the first artifact is fetched and inlined as ``content``. This
    is what makes upstream research actually visible to writer/qa/publisher
    instead of leaving them with a 200-char summary.
    """
    output = wr.get("output")
    if isinstance(output, dict):
        content = json.dumps(output)
    elif isinstance(output, str):
        content = output
    else:
        content = ""

    artifacts = wr.get("artifacts") or []
    if (not content or len(content) < 500) and artifacts:
        catalogue = get_catalogue()
        for art in artifacts:
            art_id = art.get("artifact_id") or art.get("id")
            if not art_id:
                continue
            try:
                raw, _meta = await catalogue.read(art_id)
                content = raw.decode("utf-8", errors="replace")
                break
            except (KeyError, ValueError, FileNotFoundError):
                continue

    return {
        "type": "prior_output",
        "agent_id": wr.get("agent_id", ""),
        "command": wr.get("command", ""),
        "summary": content[:200] if content else "",
        "content": content,
    }


async def prepare_wave(state: ExecutionState) -> dict[str, Any]:
    """Resolve all prior wave outputs into context entries for the next wave.

    Pulls every wave_result from earlier waves of the current phase, fetches
    catalogue artifacts where needed, and stores the resulting context list
    in ``pending_context`` so ``dispatch_wave`` can attach it to each Send.
    """
    current_phase = state["current_phase_index"]
    current_wave = state["current_wave_index"]
    prior = [
        wr
        for wr in state.get("wave_results", [])
        if wr.get("phase_index") == current_phase
        and (wr.get("wave_index") or 0) < current_wave
    ]
    # Also include results from completed earlier phases.
    prior_phase_results = [
        wr
        for wr in state.get("wave_results", [])
        if (wr.get("phase_index") or 0) < current_phase
    ]
    pending = [await _resolve_wave_result(wr) for wr in prior_phase_results + prior]
    return {"pending_context": pending}


async def agent_node(item: WaveItem) -> dict[str, Any]:
    """Execute one wave item; receives WaveItem via Send (not state)."""
    # Re-attach the execution-graph root trace context so the @agent
    # wrapper's span becomes a child of the execution root rather than
    # its own trace root. Must be paired with detach in a finally.
    carrier = item.get("trace_carrier") or {}
    token = extract_and_attach_trace_context(carrier) if carrier else None
    try:
        result = await invoke_agent(
            item["agent_id"],
            command=item["command"],
            task=item["task"],
            context=item.get("context") or [],
            trace_id=item.get("trace_id", ""),
            run_id=item.get("run_id", ""),
        )
    finally:
        if token is not None:
            detach_trace_context(token)
    signals_data = [dict(s) for s in result.signals]
    artifacts_data = [dict(a) for a in result.artifacts]

    # Andon cord: if the agent returned a failure, emit a progress event
    # so the streaming CLI surfaces it in the run log rather than silently
    # feeding empty context to QA. The wave_result still flows through as
    # data; this is purely an operator-visibility signal.
    if not result.success:
        failure_reasons = "; ".join(
            (s.get("reason") or "").splitlines()[0][:200]
            for s in signals_data
            if s.get("reason")
        )
        emit_progress(
            {
                "status": "agent failed",
                "agent": item["agent_id"],
                "command": item["command"],
                "reasons": failure_reasons,
                "signal_types": [s.get("type") for s in signals_data],
            }
        )

    entry: WaveResult = {
        "phase_index": item["phase_index"],
        "wave_index": item["wave_index"],
        "item_index": item["item_index"],
        "agent_id": item["agent_id"],
        "command": item["command"],
        "output": result.output,
        "artifacts": artifacts_data,
        "signals": signals_data,
    }
    return {"wave_results": [entry]}


async def collect_wave(state: ExecutionState) -> dict[str, Any]:
    """Filter results for current wave; flag blocking signals."""
    current_phase = state["current_phase_index"]
    current_wave = state["current_wave_index"]
    current_results = _latest_attempts(
        [
            r
            for r in state.get("wave_results", [])
            if r.get("phase_index") == current_phase
            and r.get("wave_index") == current_wave
        ]
    )

    has_blocking = any(
        in_group(s.get("type", ""), BLOCKING)
        for r in current_results
        for s in r.get("signals", [])
    )
    return {
        "signals": {
            "has_blocking_signal": has_blocking,
            "wave_item_count": len(current_results),
        }
    }


async def wave_reflection(state: ExecutionState) -> dict[str, Any]:
    """Call qa/fast to evaluate the current wave's results.

    Resolves each wave_result into a context entry (fetching catalogue
    artifacts where needed) and builds a concrete evaluation task from
    the original wave items' ``task`` strings in the work brief. A
    literal task like "Evaluate wave 0.0 results" makes QA grade the
    artifacts against that meaningless sentence; passing the original
    item tasks tells QA what "pass" actually means.
    """
    current_phase = state["current_phase_index"]
    current_wave = state["current_wave_index"]
    current_results = _latest_attempts(
        [
            r
            for r in state.get("wave_results", [])
            if r.get("phase_index") == current_phase
            and r.get("wave_index") == current_wave
        ]
    )
    qa_context = [await _resolve_wave_result(wr) for wr in current_results]

    # Pull the original item tasks from the work brief so QA knows what
    # each artifact was supposed to accomplish.
    phases = state["work_brief"].get("phases") or []
    goal = state["work_brief"].get("goal", "")
    item_tasks: list[str] = []
    try:
        phase = phases[current_phase]
        wave = (phase.get("waves") or [])[current_wave]
        for item in wave.get("items") or []:
            agent_id = item.get("agent_id", "?")
            command = item.get("command", "?")
            task_text = item.get("task", "")
            item_tasks.append(f"  - {agent_id}/{command}: {task_text}")
    except (IndexError, KeyError):
        item_tasks = []

    task_lines: list[str] = []
    if goal:
        task_lines.append(f"Overall goal: {goal}")
    task_lines.append(
        f"Evaluate whether the artifacts below satisfy the {len(item_tasks)} "
        f"task(s) assigned to this wave (phase {current_phase}, "
        f"wave {current_wave}):"
    )
    if item_tasks:
        task_lines.extend(item_tasks)
    else:
        task_lines.append("  (no item metadata available)")
    qa_task = "\n".join(task_lines)

    result = await invoke_agent(
        "qa",
        command="fast",
        task=qa_task,
        context=qa_context,
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
    for item in wave["items"]:
        if not default_registry.exists(item["agent_id"], item["command"]):
            raise SemanticError(
                type="agent_not_found",
                message=(
                    f"Agent '{item['agent_id']}/{item['command']}' is not "
                    "registered. The planner specified an agent that does not exist."
                ),
            )
    pending_context = list(state.get("pending_context") or [])
    trace_carrier = dict(state.get("trace_carrier") or {})
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
                context=pending_context,
                trace_carrier=trace_carrier,
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
    _assert_registered("qa", "fast")
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
