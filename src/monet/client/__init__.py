"""High-level client for interacting with a monet LangGraph server.

The primary interface is ``MonetClient``, which hides the three-graph
topology and provides typed event streaming, HITL action methods, and
run inspection.

Usage::

    from monet.client import MonetClient

    client = MonetClient("http://localhost:2024")
    async for event in client.run("AI trends in healthcare", auto_approve=True):
        print(event)
"""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING, Any

from monet.client._events import (
    AgentProgress,
    ExecutionInterrupt,
    PendingDecision,
    PlanApproved,
    PlanInterrupt,
    PlanReady,
    ReflectionComplete,
    RunComplete,
    RunDetail,
    RunEvent,
    RunFailed,
    RunSummary,
    TriageComplete,
    WaveComplete,
)
from monet.client._run_state import _RunState, _RunStore
from monet.client._wire import (
    _ENTRY_GRAPH,
    _EXECUTION_GRAPH,
    _PLANNING_GRAPH,
    MONET_GRAPH_KEY,
    MONET_RUN_ID_KEY,
    create_thread,
    entry_input,
    execution_input,
    get_state_values,
    make_client,
    planning_input,
    stream_run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph_sdk.client import LangGraphClient

_log = logging.getLogger("monet.client")

__all__ = [
    "AgentProgress",
    "ExecutionInterrupt",
    "MonetClient",
    "PendingDecision",
    "PlanApproved",
    "PlanInterrupt",
    "PlanReady",
    "ReflectionComplete",
    "RunComplete",
    "RunDetail",
    "RunEvent",
    "RunFailed",
    "RunSummary",
    "TriageComplete",
    "WaveComplete",
    "make_client",
]


class MonetClient:
    """Client for a monet LangGraph server.

    Provides three groups of operations:

    - **Run lifecycle** — start runs, stream progress, query history
    - **HITL decisions** — approve/revise/reject plans, retry/abort execution
    - **Results** — inspect completed runs and their artifacts
    """

    def __init__(self, url: str = "http://localhost:2024") -> None:
        self._client: LangGraphClient = make_client(url)
        self._store = _RunStore()

    # ── Run lifecycle ───────────────────────────────────────────

    async def run(
        self,
        topic: str,
        *,
        run_id: str | None = None,
        auto_approve: bool = False,
    ) -> AsyncIterator[RunEvent]:
        """Start a run and yield typed events as it progresses.

        Args:
            topic: The user's request or topic.
            run_id: Optional run identifier. Auto-generated if omitted.
            auto_approve: When True, planning interrupts are approved
                automatically. Execution interrupts always pause.

        Yields:
            ``RunEvent`` instances in order as the run progresses.
            When the run pauses for a HITL decision, the stream yields
            a ``PlanInterrupt`` or ``ExecutionInterrupt`` and ends.
            Use the HITL methods to continue.
        """
        rid = run_id or secrets.token_hex(4)
        rs = _RunState(run_id=rid)
        self._store.put(rs)

        try:
            # ── Entry / triage ──────────────────────────────────
            rs.status = "triaging"
            rs.phase = "entry"
            rs.entry_thread = await self._create_tagged_thread(rid, "entry")

            await self._drain(rs.entry_thread, _ENTRY_GRAPH, entry_input(topic, rid))
            values, _ = await get_state_values(self._client, rs.entry_thread)

            triage = values.get("triage") or {}
            yield TriageComplete(
                run_id=rid,
                complexity=triage.get("complexity", "unknown"),
                suggested_agents=triage.get("suggested_agents") or [],
            )

            if triage.get("complexity") == "simple":
                rs.status = "complete"
                yield RunComplete(run_id=rid)
                return

            # ── Planning ────────────────────────────────────────
            rs.status = "planning"
            rs.phase = "planning"
            rs.planning_thread = await self._create_tagged_thread(rid, "planning")

            await self._drain(
                rs.planning_thread,
                _PLANNING_GRAPH,
                planning_input(topic, rid),
            )
            values, nxt = await get_state_values(self._client, rs.planning_thread)

            # Handle HITL approval interrupt
            if "human_approval" in nxt:
                if auto_approve:
                    await self._drain(
                        rs.planning_thread,
                        _PLANNING_GRAPH,
                        command={"resume": {"approved": True}},
                    )
                    values, _ = await get_state_values(self._client, rs.planning_thread)
                    yield PlanApproved(run_id=rid)
                else:
                    rs.status = "interrupted"
                    brief = values.get("work_brief") or {}
                    yield PlanInterrupt(run_id=rid, brief=brief)
                    return

            if not values.get("plan_approved"):
                rs.status = "failed"
                yield RunFailed(run_id=rid, error="plan not approved")
                return

            brief = values.get("work_brief") or {}
            yield PlanReady(
                run_id=rid,
                goal=brief.get("goal", ""),
                phases=brief.get("phases") or [],
                assumptions=brief.get("assumptions") or [],
            )

            # ── Execution ───────────────────────────────────────
            async for event in self._run_execution(rs, brief, topic):
                yield event

        except Exception as exc:
            _log.exception("Run %s failed with unhandled exception", rid)
            rs.status = "failed"
            yield RunFailed(run_id=rid, error=str(exc))

    async def list_runs(self, *, limit: int = 20) -> list[RunSummary]:
        """List recent runs with status.

        Queries the server for threads tagged as monet entry threads,
        then determines each run's current status from thread state.
        """
        threads = await self._client.threads.search(
            metadata={MONET_GRAPH_KEY: "entry"},
            limit=limit,
            sort_by="created_at",
            sort_order="desc",
        )
        summaries: list[RunSummary] = []
        for t in threads:
            meta = t.get("metadata") or {}
            rid = meta.get(MONET_RUN_ID_KEY, "")
            status = str(t.get("status", "unknown"))
            phase = self._phase_from_status(rid, status)
            created = str(t.get("created_at", ""))
            summaries.append(
                RunSummary(
                    run_id=rid,
                    status=status,
                    phase=phase,
                    created_at=created,
                )
            )
        return summaries

    async def get_run(self, run_id: str) -> RunDetail:
        """Get full state of a run by inspecting all its threads."""
        triage: dict[str, Any] = {}
        work_brief: dict[str, Any] = {}
        wave_results: list[dict[str, Any]] = []
        wave_reflections: list[dict[str, Any]] = []
        status = "unknown"
        phase = "entry"

        for graph in ("entry", "planning", "execution"):
            threads = await self._client.threads.search(
                metadata={MONET_RUN_ID_KEY: run_id, MONET_GRAPH_KEY: graph},
                limit=1,
            )
            if not threads:
                continue
            tid = str(threads[0]["thread_id"])
            values, nxt = await get_state_values(self._client, tid)

            if graph == "entry":
                triage = values.get("triage") or {}
            elif graph == "planning":
                phase = "planning"
                work_brief = values.get("work_brief") or {}
            elif graph == "execution":
                phase = "execution"
                wave_results = values.get("wave_results") or []
                wave_reflections = values.get("wave_reflections") or []

            if nxt:
                status = "interrupted"
            elif graph == "execution" and wave_results:
                status = "complete"
            else:
                status = "running"

        return RunDetail(
            run_id=run_id,
            status=status,
            phase=phase,
            triage=triage,
            work_brief=work_brief,
            wave_results=wave_results,
            wave_reflections=wave_reflections,
        )

    # ── HITL decisions ──────────────────────────────────────────

    async def list_pending(self) -> list[PendingDecision]:
        """List runs currently waiting for human input."""
        threads = await self._client.threads.search(
            status="interrupted",
            metadata={MONET_RUN_ID_KEY: None},  # any monet thread
        )
        # Filter to threads that have our metadata key
        decisions: list[PendingDecision] = []
        seen_runs: set[str] = set()
        for t in threads:
            meta = t.get("metadata") or {}
            rid = meta.get(MONET_RUN_ID_KEY)
            graph = meta.get(MONET_GRAPH_KEY)
            if not rid or rid in seen_runs:
                continue
            seen_runs.add(rid)

            if graph == "planning":
                decisions.append(
                    PendingDecision(
                        run_id=rid,
                        decision_type="plan_approval",
                        summary="Plan awaiting approval",
                    )
                )
            elif graph == "execution":
                decisions.append(
                    PendingDecision(
                        run_id=rid,
                        decision_type="execution_review",
                        summary="Execution paused — blocking signal or QA failure",
                    )
                )
        return decisions

    async def approve_plan(self, run_id: str) -> AsyncIterator[RunEvent]:
        """Approve a pending plan and continue into execution.

        Yields remaining run events (execution progress and completion).
        """
        thread = await self._find_thread(run_id, "planning")
        await self._drain(
            thread,
            _PLANNING_GRAPH,
            command={"resume": {"approved": True}},
        )
        values, _ = await get_state_values(self._client, thread)
        yield PlanApproved(run_id=run_id)

        if not values.get("plan_approved"):
            yield RunFailed(run_id=run_id, error="plan not approved after resume")
            return

        brief = values.get("work_brief") or {}
        yield PlanReady(
            run_id=run_id,
            goal=brief.get("goal", ""),
            phases=brief.get("phases") or [],
            assumptions=brief.get("assumptions") or [],
        )

        rs = self._store.get(run_id) or _RunState(run_id=run_id)
        rs.planning_thread = thread
        self._store.put(rs)

        task = values.get("task", "")
        async for event in self._run_execution(rs, brief, task):
            yield event

    async def revise_plan(self, run_id: str, feedback: str) -> AsyncIterator[RunEvent]:
        """Send plan back for revision with feedback.

        Yields events as the planner revises. May yield another
        ``PlanInterrupt`` if the revised plan also needs approval.
        """
        thread = await self._find_thread(run_id, "planning")
        await self._drain(
            thread,
            _PLANNING_GRAPH,
            command={"resume": {"approved": False, "feedback": feedback}},
        )
        values, nxt = await get_state_values(self._client, thread)

        if "human_approval" in nxt:
            brief = values.get("work_brief") or {}
            yield PlanInterrupt(run_id=run_id, brief=brief)
            return

        if values.get("plan_approved"):
            brief = values.get("work_brief") or {}
            yield PlanApproved(run_id=run_id)
            yield PlanReady(
                run_id=run_id,
                goal=brief.get("goal", ""),
                phases=brief.get("phases") or [],
                assumptions=brief.get("assumptions") or [],
            )
        else:
            yield RunFailed(run_id=run_id, error="plan rejected after revision")

    async def reject_plan(self, run_id: str) -> None:
        """Reject a plan and terminate the run."""
        thread = await self._find_thread(run_id, "planning")
        await self._drain(
            thread,
            _PLANNING_GRAPH,
            command={"resume": {"approved": False, "feedback": None}},
        )
        rs = self._store.get(run_id)
        if rs:
            rs.status = "failed"

    async def retry_wave(self, run_id: str) -> AsyncIterator[RunEvent]:
        """Retry the current wave after an execution interrupt."""
        thread = await self._find_thread(run_id, "execution")
        async for event in self._stream_execution(run_id, thread, resume=True):
            yield event

    async def abort_run(self, run_id: str) -> None:
        """Abort a run during an execution interrupt."""
        thread = await self._find_thread(run_id, "execution")
        await self._drain(
            thread,
            _EXECUTION_GRAPH,
            command={"resume": {"action": "abort"}},
        )
        rs = self._store.get(run_id)
        if rs:
            rs.status = "failed"

    # ── Results ─────────────────────────────────────────────────

    async def get_results(self, run_id: str) -> RunDetail:
        """Get wave results and reflections from a run."""
        return await self.get_run(run_id)

    async def get_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        """Get all artifact pointers from a run's wave results."""
        detail = await self.get_run(run_id)
        artifacts: list[dict[str, Any]] = []
        for wr in detail.wave_results:
            for ptr in wr.get("artifacts") or []:
                artifacts.append(ptr)
        return artifacts

    # ── Private helpers ─────────────────────────────────────────

    async def _create_tagged_thread(self, run_id: str, graph: str) -> str:
        """Create a thread tagged with monet run_id and graph name."""
        return await create_thread(
            self._client,
            metadata={MONET_RUN_ID_KEY: run_id, MONET_GRAPH_KEY: graph},
        )

    async def _drain(
        self,
        thread_id: str,
        graph_id: str,
        input: dict[str, Any] | None = None,
        *,
        command: dict[str, Any] | None = None,
    ) -> None:
        """Stream a graph run to completion, discarding events."""
        async for mode, data in stream_run(
            self._client,
            thread_id,
            graph_id,
            input=input,
            command=command,
        ):
            if mode == "error":
                raise RuntimeError(f"server error: {data}")

    async def _find_thread(self, run_id: str, graph: str) -> str:
        """Find the thread ID for a specific graph phase of a run."""
        # Check local cache first
        rs = self._store.get(run_id)
        if rs:
            tid: str | None = getattr(rs, f"{graph}_thread", None)
            if tid:
                return tid

        # Search server
        threads = await self._client.threads.search(
            metadata={MONET_RUN_ID_KEY: run_id, MONET_GRAPH_KEY: graph},
            limit=1,
        )
        if not threads:
            raise ValueError(f"no {graph} thread found for run {run_id!r}")
        return str(threads[0]["thread_id"])

    async def _run_execution(
        self,
        rs: _RunState,
        brief: dict[str, Any],
        topic: str,
    ) -> AsyncIterator[RunEvent]:
        """Create an execution thread and stream it, yielding events."""
        rs.status = "executing"
        rs.phase = "execution"
        rs.execution_thread = await self._create_tagged_thread(rs.run_id, "execution")
        async for event in self._stream_execution(
            rs.run_id, rs.execution_thread, brief=brief
        ):
            yield event

    async def _stream_execution(
        self,
        run_id: str,
        thread_id: str,
        *,
        brief: dict[str, Any] | None = None,
        resume: bool = False,
    ) -> AsyncIterator[RunEvent]:
        """Stream an execution graph run and yield typed events."""
        if resume:
            input_data = None
            command = {"resume": {"action": None}}
        else:
            input_data = execution_input(brief or {}, run_id)
            command = None

        # Collect streaming events
        last_wave_index = -1
        async for mode, data in stream_run(
            self._client,
            thread_id,
            _EXECUTION_GRAPH,
            input=input_data,
            command=command,
        ):
            if mode == "error":
                yield RunFailed(run_id=run_id, error=str(data))
                return
            if mode == "custom" and isinstance(data, dict):
                agent = data.get("agent", "")
                status = data.get("status", "")
                if agent:
                    yield AgentProgress(run_id=run_id, agent_id=agent, status=status)

        # Post-stream: inspect final state
        values, nxt = await get_state_values(self._client, thread_id)
        wave_results = values.get("wave_results") or []
        wave_reflections = values.get("wave_reflections") or []

        # Yield wave completions and reflections
        for wr in wave_results:
            wi = wr.get("wave_index", 0)
            if wi > last_wave_index:
                # Collect all results for this wave
                wave_batch = [r for r in wave_results if r.get("wave_index") == wi]
                yield WaveComplete(
                    run_id=run_id,
                    phase_index=wr.get("phase_index", 0),
                    wave_index=wi,
                    results=wave_batch,
                )
                last_wave_index = wi

        for ref in wave_reflections:
            yield ReflectionComplete(
                run_id=run_id,
                verdict=ref.get("verdict", ""),
                notes=ref.get("notes", ""),
            )

        # Check for execution interrupt
        if nxt:
            rs = self._store.get(run_id)
            if rs:
                rs.status = "interrupted"
            yield ExecutionInterrupt(
                run_id=run_id,
                reason=values.get("abort_reason") or "execution paused",
                phase_index=values.get("current_phase_index", 0),
                wave_index=values.get("current_wave_index", 0),
            )
            return

        # Complete
        rs = self._store.get(run_id)
        if rs:
            rs.status = "complete"
        yield RunComplete(
            run_id=run_id,
            wave_results=wave_results,
            wave_reflections=wave_reflections,
        )

    def _phase_from_status(self, run_id: str, thread_status: str) -> str:
        """Determine the current phase from cached state or default."""
        rs = self._store.get(run_id)
        if rs:
            return rs.phase
        return "entry"
