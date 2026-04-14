"""Execution graph — flat DAG traversal with hook-resolved work briefs.

The orchestrator is pointer-only: ``routing_skeleton`` drives traversal,
``work_brief_pointer`` is passed to each agent invocation. The worker-side
``inject_plan_context`` hook fetches the full brief and injects task content
at invocation time. The orchestrator never reads artifact content.

Ready nodes are fanned out via ``Send()`` for parallel execution. On each
collect, newly successful nodes are marked complete and the graph loops
back to dispatch more ready nodes until the DAG is complete.

Returns an uncompiled StateGraph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from langchain_core.runnables import (
    RunnableConfig,  # noqa: TC002 — needed at runtime for LangGraph signature introspection
)
from langgraph.graph import END, StateGraph
from langgraph.types import Send, interrupt
from pydantic import ValidationError

from monet import emit_progress
from monet.core.tracing import (
    EXECUTION_ROOT_SPAN_NAME,
    attached_trace,
    extract_carrier_from_config,
    get_tracer,
    inject_trace_context,
)
from monet.types import ArtifactPointer  # noqa: TC001 — runtime type for TypedDict

from ._invoke import invoke_agent
from ._signal_router import EXECUTION_ROUTER
from ._state import ExecutionState, RoutingSkeleton, SignalsSummary

if TYPE_CHECKING:
    from monet.core.hooks import GraphHookRegistry


AGENT_FAILED_EVENT_STATUS = "agent failed"


class NodeItem(TypedDict):
    """Payload sent to ``agent_node`` via ``Send()``.

    The worker-side ``inject_plan_context`` hook resolves the full task
    content from ``work_brief_pointer`` + ``node_id`` before the agent
    runs. ``upstream_results`` carries lightweight pointers to each
    ``depends_on`` node's output so the agent can see what upstream
    produced — the agent resolves artifact content via ``resolve_context``.
    """

    node_id: str
    agent_id: str
    command: str
    work_brief_pointer: ArtifactPointer
    upstream_results: list[dict[str, Any]]
    trace_id: str
    run_id: str
    trace_carrier: dict[str, str]


async def initialise_execution(
    state: ExecutionState, config: RunnableConfig
) -> dict[str, Any]:
    """Validate state, attach trace context. No artifact read."""
    skeleton_raw = state.get("routing_skeleton")
    if not skeleton_raw:
        return {"abort_reason": "No routing_skeleton in execution state."}
    try:
        skeleton = RoutingSkeleton.model_validate(skeleton_raw)
    except ValidationError as exc:
        return {"abort_reason": f"Invalid routing_skeleton: {exc}"}

    pointer = state.get("work_brief_pointer")
    if not pointer:
        return {"abort_reason": "No work_brief_pointer in execution state."}

    # Attach upstream trace context and open the execution-graph root span.
    upstream_carrier = extract_carrier_from_config(config)
    async with attached_trace(upstream_carrier):
        tracer = get_tracer(EXECUTION_ROOT_SPAN_NAME)
        with tracer.start_as_current_span(
            EXECUTION_ROOT_SPAN_NAME,
            attributes={
                "monet.run_id": state.get("run_id", ""),
                "monet.trace_id": state.get("trace_id", ""),
                "monet.node_count": len(skeleton.nodes),
                "monet.goal": skeleton.goal[:200],
            },
        ):
            carrier = inject_trace_context()

    return {
        "completed_node_ids": [],
        "wave_results": [],
        "wave_reflections": [],
        "signals": None,
        "abort_reason": None,
        "trace_carrier": carrier,
    }


def dispatch_ready_nodes(state: ExecutionState) -> list[Send] | str:
    """Fan out ready nodes via Send, or END on abort/completion.

    Determining which nodes are ready uses ``RoutingSkeleton.ready_nodes``
    against the current ``completed_node_ids`` set.
    """
    if state.get("abort_reason"):
        return END
    skeleton = RoutingSkeleton.model_validate(state["routing_skeleton"])
    completed = set(state.get("completed_node_ids") or [])
    if skeleton.is_complete(completed):
        return END
    ready = skeleton.ready_nodes(completed)
    if not ready:
        # DAG is not complete but no nodes are ready — upstream failure
        # blocked progress. Terminate cleanly.
        return END

    pointer = state["work_brief_pointer"]
    trace_carrier = dict(state.get("trace_carrier") or {})
    wave_results = state.get("wave_results") or []
    results_by_id = {r["node_id"]: r for r in wave_results}
    deps_by_id = {n.id: list(n.depends_on) for n in skeleton.nodes}
    return [
        Send(
            "agent_node",
            NodeItem(
                node_id=node.id,
                agent_id=node.agent_id,
                command=node.command,
                work_brief_pointer=pointer,
                upstream_results=_upstream_entries(node.id, deps_by_id, results_by_id),
                trace_id=state.get("trace_id", ""),
                run_id=state.get("run_id", ""),
                trace_carrier=trace_carrier,
            ),
        )
        for node in ready
    ]


def _upstream_entries(
    node_id: str,
    deps_by_id: dict[str, list[str]],
    results_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build upstream-result entries for every transitive ancestor of ``node_id``.

    Each entry carries artifact pointers so ``resolve_context`` can fetch
    full content on the worker side. Transitive ancestors are included so
    that, e.g., a publisher that depends only on QA still sees the
    draft-producing writer's artifact.

    Ordered root-first (topological) so agent prompts read naturally.
    """
    ancestors: list[str] = []
    seen: set[str] = set()
    stack = list(deps_by_id.get(node_id, []))
    while stack:
        current = stack.pop(0)
        if current in seen:
            continue
        seen.add(current)
        ancestors.append(current)
        stack.extend(deps_by_id.get(current, []))
    ancestors.reverse()  # root-first

    entries: list[dict[str, Any]] = []
    for ancestor_id in ancestors:
        result = results_by_id.get(ancestor_id)
        if result is None:
            continue
        output = result.get("output") or ""
        summary = output[:200] if isinstance(output, str) else ""
        entries.append(
            {
                "type": "upstream_result",
                "node_id": result.get("node_id", ancestor_id),
                "agent_id": result.get("agent_id", ""),
                "command": result.get("command", ""),
                "summary": summary,
                "artifacts": result.get("artifacts") or [],
            }
        )
    return entries


async def agent_node(item: NodeItem) -> dict[str, Any]:
    """Execute a single DAG node.

    Passes only a ``plan_item`` context entry — the task content is
    resolved by the worker-side ``inject_plan_context`` hook at agent
    invocation time. The orchestrator never reads the full work brief.
    """
    async with attached_trace(item.get("trace_carrier")):
        result = await invoke_agent(
            item["agent_id"],
            command=item["command"],
            task="",  # injected by the inject_plan_context hook
            context=[
                {
                    "type": "plan_item",
                    "work_brief_pointer": item["work_brief_pointer"],
                    "node_id": item["node_id"],
                },
                *item.get("upstream_results", []),
            ],
            trace_id=item.get("trace_id", ""),
            run_id=item.get("run_id", ""),
        )

    signals_data = [dict(s) for s in result.signals]
    artifacts_data = [dict(a) for a in result.artifacts]

    if not result.success:
        failure_reasons = "; ".join(
            str(s.get("reason", "")).splitlines()[0][:200]
            for s in signals_data
            if s.get("reason")
        )
        emit_progress(
            {
                "status": AGENT_FAILED_EVENT_STATUS,
                "agent": item["agent_id"],
                "command": item["command"],
                "reasons": failure_reasons,
                "signal_types": [s.get("type") for s in signals_data],
            }
        )

    entry: dict[str, Any] = {
        "node_id": item["node_id"],
        "agent_id": item["agent_id"],
        "command": item["command"],
        "output": result.output,
        "artifacts": artifacts_data,
        "signals": signals_data,
        "success": result.success,
    }
    return {"wave_results": [entry]}


async def collect_batch(state: ExecutionState) -> dict[str, Any]:
    """Merge node results, mark completed, summarise signals."""
    all_results = state.get("wave_results") or []
    completed = set(state.get("completed_node_ids") or [])
    # "New" results are those not yet reflected in completed_node_ids
    # and not in the prior failure tracker.
    new_results = [
        r
        for r in all_results
        if r.get("node_id")
        and r["node_id"] not in completed
        and not _is_prior_failure(state, r)
    ]

    newly_completed = [r["node_id"] for r in new_results if r.get("success")]
    all_completed = list(completed | set(newly_completed))

    all_signals = [s for r in new_results for s in r.get("signals", [])]
    route = EXECUTION_ROUTER.route(all_signals)
    summary: SignalsSummary = {
        "route_action": route.action if route else None,
        "wave_item_count": len(new_results),
    }

    update: dict[str, Any] = {
        "completed_node_ids": all_completed,
        "signals": summary,
    }

    # Any failure without a routed recovery path aborts the run. The
    # planner can insert explicit retry or QA nodes into the DAG for
    # more sophisticated failure handling.
    failures = [r for r in new_results if not r.get("success")]
    if failures and summary["route_action"] != "interrupt":
        reasons = "; ".join(
            f"{r['node_id']}: {(r.get('signals') or [{}])[0].get('reason', '')}"[:200]
            for r in failures
        )
        update["abort_reason"] = f"Node failure: {reasons}"

    return update


def _is_prior_failure(state: ExecutionState, result: dict[str, Any]) -> bool:
    """Skip results already accounted for in a prior abort (should not re-handle)."""
    # For now: rely on append-only wave_results. collect_batch is only
    # entered after a new batch of Send() calls, so "new" results are
    # those since the last collect. Track via a cursor on `signals`
    # wave_item_count if needed — for v1, we use the completed_node_ids
    # set and assume no result is reprocessed after success.
    return False


def route_after_collect(state: ExecutionState) -> str:
    """Route after collecting a batch."""
    if state.get("abort_reason"):
        return END
    signals = state.get("signals") or {}
    if signals.get("route_action") == "interrupt":
        return "human_interrupt"
    skeleton = RoutingSkeleton.model_validate(state["routing_skeleton"])
    completed = set(state.get("completed_node_ids") or [])
    if skeleton.is_complete(completed):
        return END
    return "dispatch"


async def dispatch_node(state: ExecutionState) -> dict[str, Any]:
    """Pass-through node so dispatch_ready_nodes can be a conditional edge.

    Emits a per-batch before-dispatch hook point if hooks are registered.
    """
    return {}


async def human_interrupt(state: ExecutionState) -> dict[str, Any]:
    """Pause for human decision after a blocking signal."""
    results = state.get("wave_results") or []
    last = results[-1] if results else {}
    decision = interrupt(
        {
            "reason": "Blocking signal from node execution",
            "last_result": last,
        }
    )
    if isinstance(decision, dict) and decision.get("action") == "abort":
        return {"abort_reason": decision.get("feedback", "Aborted by human")}
    return {}


def route_after_interrupt(state: ExecutionState) -> str:
    if state.get("abort_reason"):
        return END
    return "dispatch"


def build_execution_graph(
    hooks: GraphHookRegistry | None = None,
) -> StateGraph[ExecutionState]:
    """Build the flat-DAG execution graph. Returns uncompiled StateGraph.

    Args:
        hooks: Optional graph hook registry. Fires ``before_wave`` before
            each batch dispatch and ``after_wave_server`` after each
            collection.
    """
    _dispatch_inner = dispatch_node
    _collect_inner = collect_batch

    async def _dispatch_with_hooks(state: ExecutionState) -> dict[str, Any]:
        update = await _dispatch_inner(state)
        if hooks:
            wave_ctx: dict[str, Any] = {
                "completed_node_ids": list(state.get("completed_node_ids") or []),
            }
            await hooks.run("before_wave", wave_ctx)
        return update

    async def _collect_with_hooks(state: ExecutionState) -> dict[str, Any]:
        update = await _collect_inner(state)
        if hooks:
            update["signals"] = await hooks.run("after_wave_server", update["signals"])
        return update

    dispatch_wrap = _dispatch_with_hooks if hooks else dispatch_node
    collect_wrap = _collect_with_hooks if hooks else collect_batch

    graph = StateGraph(ExecutionState)
    graph.add_node("initialise_execution", initialise_execution)
    graph.add_node("dispatch", dispatch_wrap)
    graph.add_node("agent_node", agent_node)  # type: ignore[arg-type]
    graph.add_node("collect_batch", collect_wrap)
    graph.add_node("human_interrupt", human_interrupt)

    graph.set_entry_point("initialise_execution")
    graph.add_edge("initialise_execution", "dispatch")
    graph.add_conditional_edges("dispatch", dispatch_ready_nodes, ["agent_node", END])
    graph.add_edge("agent_node", "collect_batch")
    graph.add_conditional_edges(
        "collect_batch",
        route_after_collect,
        {
            "dispatch": "dispatch",
            "human_interrupt": "human_interrupt",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "human_interrupt",
        route_after_interrupt,
        {"dispatch": "dispatch", END: END},
    )
    return graph
