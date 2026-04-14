"""Default pipeline adapter — composes entry → planning → execution.

The adapter owns:

- Thread lifecycle for each stage (tagged with shared ``run_id``)
- HITL plan-approval flow (auto-approve or surface ``PlanInterrupt``)
- Wave-batching of execution results
- Projection of generic core events to the
  :class:`~monet.pipelines.default.events.DefaultPipelineEvent` union

It uses the adapter API in :mod:`monet.client._wire` directly; the
generic :meth:`MonetClient.run` is for single-graph invocations.
"""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING, Any

from monet.client._wire import (
    MONET_GRAPH_KEY,
    MONET_RUN_ID_KEY,
    create_thread,
    get_state_values,
    stream_run,
    task_input,
)
from monet.pipelines.default._inputs import execution_input, planning_input
from monet.pipelines.default.events import (
    ExecutionInterrupt,
    PlanApproved,
    PlanInterrupt,
    PlanReady,
    ReflectionComplete,
    TriageComplete,
    WaveComplete,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph_sdk.client import LangGraphClient

    from monet.client import MonetClient
    from monet.client._events import RunComplete, RunFailed
    from monet.pipelines.default.events import DefaultPipelineEvent

_log = logging.getLogger("monet.pipelines.default")


async def run(
    client: MonetClient,
    topic: str,
    *,
    run_id: str | None = None,
    auto_approve: bool = False,
) -> AsyncIterator[DefaultPipelineEvent | RunComplete | RunFailed]:
    """Drive the default pipeline and yield typed events.

    Args:
        client: A :class:`MonetClient` connected to the server.
        topic: The user's request.
        run_id: Optional run identifier. Auto-generated if omitted.
        auto_approve: When True, planning interrupts are approved
            automatically. Execution interrupts always pause.

    Yields:
        :class:`DefaultPipelineEvent` domain events plus
        :class:`RunComplete` / :class:`RunFailed` from the core union.
        When paused for HITL, the stream yields :class:`PlanInterrupt`
        or :class:`ExecutionInterrupt` and ends — use
        :mod:`monet.pipelines.default._hitl` to continue.
    """
    # Lazy imports to avoid circular imports at module load.
    from monet.client._events import RunComplete, RunFailed

    rid = run_id or secrets.token_hex(4)
    sdk = client._client

    try:
        # ── Entry / triage ──────────────────────────────────────────
        entry_thread = await _tagged_thread(sdk, rid, "entry")
        client._store.put_thread(rid, "entry", entry_thread)
        await _drain(sdk, entry_thread, "entry", input=task_input(topic, rid))
        values, _ = await get_state_values(sdk, entry_thread)
        triage = values.get("triage") or {}

        yield TriageComplete(
            run_id=rid,
            complexity=triage.get("complexity", "unknown"),
            suggested_agents=triage.get("suggested_agents") or [],
        )

        if triage.get("complexity") == "simple":
            yield RunComplete(run_id=rid)
            return

        # ── Planning ────────────────────────────────────────────────
        planning_thread = await _tagged_thread(sdk, rid, "planning")
        client._store.put_thread(rid, "planning", planning_thread)
        await _drain(sdk, planning_thread, "planning", input=planning_input(topic, rid))
        values, nxt = await get_state_values(sdk, planning_thread)

        if "human_approval" in nxt:
            if auto_approve:
                await _drain(
                    sdk,
                    planning_thread,
                    "planning",
                    command={"resume": {"approved": True}},
                )
                values, _ = await get_state_values(sdk, planning_thread)
                yield PlanApproved(run_id=rid)
            else:
                yield PlanInterrupt(
                    run_id=rid,
                    work_brief_pointer=values.get("work_brief_pointer"),
                    routing_skeleton=values.get("routing_skeleton") or {},
                )
                return

        if not values.get("plan_approved"):
            error = values.get("planner_error") or "plan not approved"
            yield RunFailed(run_id=rid, error=error)
            return

        pointer = values.get("work_brief_pointer")
        skeleton = values.get("routing_skeleton") or {}
        if not pointer or not skeleton:
            yield RunFailed(
                run_id=rid,
                error=(
                    values.get("planner_error")
                    or "planner produced no work_brief_pointer/routing_skeleton"
                ),
            )
            return

        yield PlanReady(
            run_id=rid,
            goal=skeleton.get("goal", ""),
            nodes=skeleton.get("nodes") or [],
        )

        # ── Execution ───────────────────────────────────────────────
        async for ev in _drive_execution(client, rid, pointer, skeleton):
            yield ev

    except Exception as exc:
        _log.exception("default pipeline run %s failed", rid)
        yield RunFailed(run_id=rid, error=str(exc))


async def _drive_execution(
    client: MonetClient,
    run_id: str,
    pointer: Any,
    skeleton: dict[str, Any],
) -> AsyncIterator[DefaultPipelineEvent | RunComplete | RunFailed]:
    """Create an execution thread and stream it, yielding events."""
    from monet.client._events import RunComplete

    sdk = client._client

    exec_thread = await _tagged_thread(sdk, run_id, "execution")
    client._store.put_thread(run_id, "execution", exec_thread)
    await _drain(
        sdk,
        exec_thread,
        "execution",
        input=execution_input(pointer, skeleton, run_id),
    )

    async for ev in _emit_execution_events(sdk, run_id, exec_thread, skeleton):
        yield ev

    values, nxt = await get_state_values(sdk, exec_thread)
    if nxt:
        return  # ExecutionInterrupt already yielded above
    yield RunComplete(
        run_id=run_id,
        final_values={
            "wave_results": values.get("wave_results") or [],
            "wave_reflections": values.get("wave_reflections") or [],
        },
    )


async def _emit_execution_events(
    sdk: LangGraphClient,
    run_id: str,
    thread_id: str,
    skeleton: dict[str, Any],
) -> AsyncIterator[DefaultPipelineEvent]:
    """Inspect execution thread state and emit wave/reflection/interrupt events."""
    values, nxt = await get_state_values(sdk, thread_id)
    wave_results = values.get("wave_results") or []
    wave_reflections = values.get("wave_reflections") or []
    skeleton_for_waves = values.get("routing_skeleton") or skeleton or {}

    # Group wave_results into dispatch batches. The flat-DAG executor
    # emits wave_results strictly in batch order (initialise → dispatch
    # → agent_node xN → collect_batch → dispatch → ...). Each new batch
    # starts after the prior batch's completed nodes are observed.
    if wave_results:
        completed_set: set[str] = set()
        batch: list[dict[str, Any]] = []
        wave_index = 0
        for wr in wave_results:
            deps = {
                d
                for node in (skeleton_for_waves.get("nodes") or [])
                if node.get("id") == wr.get("node_id")
                for d in (node.get("depends_on") or [])
            }
            if batch and deps and not deps.issubset(completed_set):
                yield WaveComplete(
                    run_id=run_id,
                    wave_index=wave_index,
                    node_ids=[r.get("node_id", "") for r in batch],
                    results=batch,
                )
                for r in batch:
                    if r.get("success"):
                        completed_set.add(r.get("node_id", ""))
                batch = []
                wave_index += 1
            batch.append(wr)
        if batch:
            yield WaveComplete(
                run_id=run_id,
                wave_index=wave_index,
                node_ids=[r.get("node_id", "") for r in batch],
                results=batch,
            )

    for ref in wave_reflections:
        yield ReflectionComplete(
            run_id=run_id,
            verdict=ref.get("verdict", ""),
            notes=ref.get("notes", ""),
        )

    if nxt:
        # Execution interrupt pending — surface payload shape from
        # execution_graph.human_interrupt (reason, last_result, pending_node_ids).
        # The interrupt node doesn't echo to state; reconstruct pending ids.
        skeleton_state = values.get("routing_skeleton") or {}
        all_ids = [n.get("id", "") for n in (skeleton_state.get("nodes") or [])]
        completed = set(values.get("completed_node_ids") or [])
        pending = [nid for nid in all_ids if nid and nid not in completed]
        last = wave_results[-1] if wave_results else {}
        yield ExecutionInterrupt(
            run_id=run_id,
            reason=values.get("abort_reason") or "execution paused",
            last_result=last,
            pending_node_ids=pending,
        )


async def _tagged_thread(sdk: LangGraphClient, run_id: str, graph: str) -> str:
    """Create a thread tagged with the run_id and graph name."""
    return await create_thread(
        sdk,
        metadata={MONET_RUN_ID_KEY: run_id, MONET_GRAPH_KEY: graph},
    )


async def _drain(
    sdk: LangGraphClient,
    thread_id: str,
    graph_id: str,
    *,
    input: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
) -> None:
    """Stream a graph run to completion, discarding events."""
    async for mode, data in stream_run(
        sdk,
        thread_id,
        graph_id,
        input=input,
        command=command,
    ):
        if mode == "error":
            raise RuntimeError(f"server error: {data}")


__all__ = ["run"]
