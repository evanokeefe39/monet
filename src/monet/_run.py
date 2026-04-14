"""In-process run — the local equivalent of ``MonetClient.run()``.

Wires bootstrap, builds the three graphs, runs them sequentially with
auto-approval, and yields the same typed events as ``MonetClient``.
No server required.

Usage::

    from monet import run

    async for event in run("AI trends in healthcare"):
        print(event)
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from typing import TYPE_CHECKING, Any

from monet.client._events import (
    PlanApproved,
    PlanReady,
    ReflectionComplete,
    RunComplete,
    RunFailed,
    TriageComplete,
    WaveComplete,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from monet.client._events import RunEvent


async def run(
    topic: str,
    *,
    run_id: str | None = None,
    enable_tracing: bool = False,
) -> AsyncIterator[RunEvent]:
    """Run the full pipeline in-process and yield typed events.

    This is the local equivalent of ``MonetClient().run(topic)``.
    No server needed — everything runs in the current process.

    Args:
        topic: The user's request or topic.
        run_id: Optional run identifier. Auto-generated if omitted.
        enable_tracing: Whether to configure OpenTelemetry tracing.

    Yields:
        ``RunEvent`` instances matching the ``MonetClient`` stream.
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    import monet.agents  # noqa: F401 — registers reference agents
    from monet.orchestration import (
        build_entry_graph,
        build_execution_graph,
        build_planning_graph,
    )
    from monet.server import bootstrap

    rid = run_id or secrets.token_hex(4)
    worker = await bootstrap(enable_tracing=enable_tracing)
    ck = MemorySaver()

    try:
        # ── Triage ──────────────────────────────────────────────
        entry_state = (
            await build_entry_graph()
            .compile(checkpointer=ck)
            .ainvoke(  # type: ignore[call-overload]
                {"task": topic, "trace_id": rid, "run_id": rid},
                config={"configurable": {"thread_id": f"{rid}-entry"}},
            )
        )
        triage = entry_state.get("triage") or {}
        yield TriageComplete(
            run_id=rid,
            complexity=triage.get("complexity", "unknown"),
            suggested_agents=triage.get("suggested_agents") or [],
        )
        if triage.get("complexity") == "simple":
            yield RunComplete(run_id=rid)
            return

        # ── Planning (auto-approve) ─────────────────────────────
        planning = build_planning_graph().compile(checkpointer=ck)
        cfg = {"configurable": {"thread_id": f"{rid}-planning"}}
        await planning.ainvoke(  # type: ignore[call-overload]
            {"task": topic, "trace_id": rid, "run_id": rid, "revision_count": 0},
            config=cfg,
        )
        planning_state = await planning.ainvoke(  # type: ignore[call-overload]
            Command(resume={"approved": True, "feedback": None}),
            config=cfg,
        )

        if not planning_state.get("plan_approved"):
            yield RunFailed(run_id=rid, error="plan not approved")
            return

        pointer = planning_state.get("work_brief_pointer")
        skeleton_raw = planning_state.get("routing_skeleton")
        if pointer is None or skeleton_raw is None:
            yield RunFailed(
                run_id=rid,
                error="plan approved but missing pointer or routing_skeleton",
            )
            return

        from monet.orchestration._state import RoutingSkeleton

        skeleton = RoutingSkeleton.model_validate(skeleton_raw)
        yield PlanApproved(run_id=rid)
        yield PlanReady(
            run_id=rid,
            goal=skeleton.goal,
            nodes=[
                {
                    "id": n.id,
                    "agent_id": n.agent_id,
                    "command": n.command,
                    "depends_on": list(n.depends_on),
                }
                for n in skeleton.nodes
            ],
        )

        # ── Execution — pointer-only, DAG traversal ─────────────
        exec_state = (
            await build_execution_graph()
            .compile(checkpointer=ck)
            .ainvoke(  # type: ignore[call-overload]
                {
                    "work_brief_pointer": pointer,
                    "routing_skeleton": skeleton_raw,
                    "trace_id": rid,
                    "run_id": rid,
                },
                config={"configurable": {"thread_id": f"{rid}-exec"}},
            )
        )

        wave_results: list[dict[str, Any]] = exec_state.get("wave_results") or []
        wave_reflections: list[dict[str, Any]] = (
            exec_state.get("wave_reflections") or []
        )

        # Emit a single WaveComplete carrying all node results — the
        # new execution graph doesn't batch by wave index, so collapse
        # the stream into one event for client compatibility.
        if wave_results:
            yield WaveComplete(
                run_id=rid,
                wave_index=0,
                node_ids=[str(r.get("node_id", "")) for r in wave_results],
                results=wave_results,
            )

        for ref in wave_reflections:
            yield ReflectionComplete(
                run_id=rid,
                verdict=ref.get("verdict", ""),
                notes=ref.get("notes", ""),
            )

        if exec_state.get("abort_reason"):
            yield RunFailed(run_id=rid, error=exec_state["abort_reason"])
            return

        yield RunComplete(
            run_id=rid,
            wave_results=wave_results,
            wave_reflections=wave_reflections,
        )

    except Exception as exc:
        yield RunFailed(run_id=rid, error=str(exc))

    finally:
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
