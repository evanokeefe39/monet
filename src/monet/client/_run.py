"""RunClient — run lifecycle and HITL operations."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import TYPE_CHECKING, Any

from monet.client._errors import (
    AlreadyResolved,
    AmbiguousInterrupt,
    GraphNotInvocable,
    InterruptTagMismatch,
    MonetClientError,
    RunNotInterrupted,
)
from monet.client._events import (
    AgentProgress,
    Interrupt,
    NodeUpdate,
    PendingDecision,
    RunComplete,
    RunDetail,
    RunFailed,
    RunStarted,
    RunSummary,
    SignalEmitted,
)
from monet.client._wire import (
    MONET_GRAPH_KEY,
    MONET_RUN_ID_KEY,
    create_thread,
    get_state_values,
    stream_run,
    task_input,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from monet.client._core import _ClientCore
    from monet.client._events import RunEvent

_log = logging.getLogger("monet.client")


def _extract_interrupt_payload(state: Any) -> dict[str, Any]:
    """Pull the first interrupt payload off a LangGraph state snapshot."""

    def _get(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    tasks = _get(state, "tasks") or []
    for task in tasks:
        interrupts = _get(task, "interrupts") or []
        for interrupt_item in interrupts:
            value = _get(interrupt_item, "value")
            if isinstance(value, dict):
                return value
    values = _get(state, "values") or {}
    if isinstance(values, dict):
        fallback = values.get("__interrupt__")
        if isinstance(fallback, dict):
            return fallback
    return {}


def _build_agent_progress(run_id: str, data: dict[str, Any]) -> AgentProgress | None:
    """Convert a custom-stream wire dict into an AgentProgress."""
    agent = data.get("agent", "")
    if not agent:
        return None
    return AgentProgress(
        run_id=run_id,
        agent_id=agent,
        status=data.get("status", ""),
        command=data.get("command", ""),
        reasons=data.get("reasons", ""),
    )


def _build_signal_emitted(run_id: str, data: dict[str, Any]) -> SignalEmitted | None:
    """Convert a custom-stream wire dict into a SignalEmitted."""
    signal_type = data.get("signal_type") or data.get("type")
    agent = data.get("agent", "")
    if not signal_type or not agent:
        return None
    _skip = {"signal_type", "type", "agent"}
    payload = {k: v for k, v in data.items() if k not in _skip}
    return SignalEmitted(
        run_id=run_id,
        agent_id=agent,
        signal_type=str(signal_type),
        payload=payload,
    )


class RunClient:
    """Run lifecycle and HITL operations."""

    def __init__(self, core: _ClientCore) -> None:
        self._core = core

    async def run(
        self,
        graph_id: str,
        input: dict[str, Any] | str | None = None,
        *,
        run_id: str | None = None,
    ) -> AsyncIterator[RunEvent]:
        declared_graphs = {ep["graph"] for ep in self._core.entrypoints.values()}
        if graph_id not in declared_graphs:
            raise GraphNotInvocable(graph_id, sorted(declared_graphs))

        rid = run_id or secrets.token_hex(4)
        if isinstance(input, str):
            input = task_input(input, rid)

        thread = await create_thread(
            self._core.client,
            metadata={MONET_RUN_ID_KEY: rid, MONET_GRAPH_KEY: graph_id},
        )
        self._core.store.put_thread(rid, graph_id, thread)
        yield RunStarted(run_id=rid, graph_id=graph_id, thread_id=thread)

        active_rid = rid
        try:
            async for mode, data in stream_run(
                self._core.client, thread, graph_id, input=input or {}
            ):
                if mode == "metadata":
                    if isinstance(data, dict):
                        active_rid = data.get("run_id", active_rid)
                    continue

                if mode == "error":
                    yield RunFailed(run_id=active_rid, error=str(data))
                    return
                if mode == "custom" and isinstance(data, dict):
                    signal = _build_signal_emitted(active_rid, data)
                    if signal is not None:
                        yield signal
                        continue
                    progress = _build_agent_progress(active_rid, data)
                    if progress is not None:
                        yield progress
                        continue
                elif mode == "updates" and isinstance(data, dict):
                    for node_name, update in data.items():
                        if isinstance(update, dict):
                            yield NodeUpdate(
                                run_id=active_rid, node=node_name, update=update
                            )

            values, nxt = await get_state_values(self._core.client, thread)
            if nxt:
                tag = nxt[0]
                yield Interrupt(
                    run_id=rid,
                    tag=tag,
                    values=values.get("__interrupt__") or {},
                    next_nodes=list(nxt),
                )
                return
            yield RunComplete(run_id=rid, final_values=values)
        except Exception as exc:
            _log.exception("run %s on %s failed", rid, graph_id)
            yield RunFailed(run_id=rid, error=str(exc))

    async def resume(
        self,
        run_id: str,
        tag: str,
        payload: dict[str, Any],
    ) -> None:
        thread, graph_id = await self._find_interrupted_thread(run_id)
        _, nxt = await get_state_values(self._core.client, thread)
        if not nxt:
            raise AlreadyResolved(run_id)
        if len(nxt) > 1:
            raise AmbiguousInterrupt(run_id, list(nxt))
        if nxt[0] != tag:
            raise InterruptTagMismatch(run_id, expected=nxt[0], got=tag)

        await self._await_interrupted_status(thread)

        if self._core.data_url != self._core.url:
            await self._post_hitl_decision(run_id, tag)

        _log.info(
            "resume",
            extra={"run_id": run_id, "tag": tag, "payload_keys": list(payload)},
        )
        async for mode, data in stream_run(
            self._core.client,
            thread,
            graph_id,
            command={"resume": payload},
        ):
            if mode == "error":
                raise RuntimeError(f"server error: {data}")

    async def abort(self, run_id: str) -> None:
        thread, graph_id = await self._find_interrupted_thread(run_id)
        async for mode, data in stream_run(
            self._core.client,
            thread,
            graph_id,
            command={"resume": {"action": "abort"}},
        ):
            if mode == "error":
                raise RuntimeError(f"server error: {data}")

    async def list_runs(self, *, limit: int = 20) -> list[RunSummary]:
        threads = await self._core.client.threads.search(
            metadata={MONET_RUN_ID_KEY: None},
            limit=limit * 4,
            sort_by="created_at",
            sort_order="desc",
        )
        per_run: dict[str, list[dict[str, Any]]] = {}
        for raw in threads:
            t: dict[str, Any] = dict(raw)  # type: ignore[call-overload]
            meta = t.get("metadata") or {}
            rid = meta.get(MONET_RUN_ID_KEY)
            if not isinstance(rid, str):
                continue
            per_run.setdefault(rid, []).append(t)

        summaries: list[RunSummary] = []
        for rid, ts in per_run.items():
            ts.sort(key=lambda t: str(t.get("created_at", "")))
            head = ts[-1]
            stages = [
                str((t.get("metadata") or {}).get(MONET_GRAPH_KEY, ""))
                for t in ts
                if (t.get("metadata") or {}).get(MONET_GRAPH_KEY)
            ]
            summaries.append(
                RunSummary(
                    run_id=rid,
                    status=str(head.get("status", "unknown")),
                    completed_stages=stages,
                    created_at=str(ts[0].get("created_at", "")),
                )
            )
            if len(summaries) >= limit:
                break
        return summaries

    async def get_run(self, run_id: str) -> RunDetail:
        threads = await self._core.client.threads.search(
            metadata={MONET_RUN_ID_KEY: run_id},
            limit=20,
        )
        threads.sort(key=lambda t: str(t.get("created_at", "")))

        merged_values: dict[str, Any] = {}
        completed: list[str] = []
        status = "unknown"
        pending: Interrupt | None = None

        for t in threads:
            tid = str(t.get("thread_id", ""))
            if not tid:
                continue
            meta = t.get("metadata") or {}
            graph = str(meta.get(MONET_GRAPH_KEY, ""))
            if graph:
                completed.append(graph)

            values, nxt = await get_state_values(self._core.client, tid)
            merged_values.update(values)
            status = str(t.get("status", status))
            if nxt:
                status = "interrupted"
                pending = Interrupt(
                    run_id=run_id,
                    tag=nxt[0],
                    values=values.get("__interrupt__") or {},
                    next_nodes=list(nxt),
                )
        return RunDetail(
            run_id=run_id,
            status=status,
            completed_stages=completed,
            values=merged_values,
            pending_interrupt=pending,
        )

    async def list_pending(self) -> list[PendingDecision]:
        threads = await self._core.client.threads.search(
            status="interrupted",
            metadata={MONET_RUN_ID_KEY: None},
        )
        decisions: list[PendingDecision] = []
        seen: set[str] = set()
        for t in threads:
            meta = t.get("metadata") or {}
            rid = meta.get(MONET_RUN_ID_KEY)
            if not isinstance(rid, str) or rid in seen:
                continue
            seen.add(rid)
            tid = str(t.get("thread_id", ""))
            _, nxt = await get_state_values(self._core.client, tid)
            tag = nxt[0] if nxt else ""
            decisions.append(PendingDecision(run_id=rid, decision_type=tag, summary=""))
        return decisions

    async def list_graphs(self) -> list[str]:
        assistants = await self._core.client.assistants.search(limit=100)
        graph_ids: list[str] = []
        seen: set[str] = set()
        for a in assistants:
            gid = a.get("graph_id", "")
            if gid and gid not in seen:
                seen.add(gid)
                graph_ids.append(gid)
        return sorted(graph_ids)

    async def _find_interrupted_thread(self, run_id: str) -> tuple[str, str]:
        cached = self._core.store.threads_for(run_id)
        if cached:
            for graph_id, thread_id in cached.items():
                _, nxt = await get_state_values(self._core.client, thread_id)
                if nxt:
                    return thread_id, graph_id

        threads = await self._core.client.threads.search(
            metadata={MONET_RUN_ID_KEY: run_id},
            status="interrupted",
            limit=5,
        )
        if not threads:
            raise RunNotInterrupted(run_id)
        t = threads[0]
        tid = str(t.get("thread_id", ""))
        graph_id = str((t.get("metadata") or {}).get(MONET_GRAPH_KEY, ""))
        if not tid:
            raise RunNotInterrupted(run_id)
        return tid, graph_id

    async def _post_hitl_decision(self, run_id: str, tag: str) -> None:
        import contextlib

        from monet.client._wire import post_hitl_decision, query_progress_events

        events = await query_progress_events(
            self._core.data_url, run_id, api_key=self._core.api_key, after=0, limit=500
        )
        cause_id: str | None = None
        task_id = ""
        agent_id = ""
        for ev in reversed(events):
            if ev.get("event_type") == "hitl_cause":
                cause_id = (ev.get("payload") or {}).get("cause_id")
                task_id = ev.get("task_id", "")
                agent_id = ev.get("agent_id", "")
                break

        if not cause_id:
            return

        with contextlib.suppress(AlreadyResolved):
            await post_hitl_decision(
                self._core.data_url,
                run_id,
                task_id=task_id,
                agent_id=agent_id,
                cause_id=cause_id,
                tag=tag,
                api_key=self._core.api_key,
            )

    async def _await_interrupted_status(
        self,
        thread_id: str,
        *,
        timeout: float = 3.0,
        interval: float = 0.05,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            thread = await self._core.client.threads.get(thread_id)
            if thread.get("status") == "interrupted":
                return
            if loop.time() >= deadline:
                raise MonetClientError(
                    f"thread {thread_id!r} did not reach "
                    f"'interrupted' status within {timeout}s"
                )
            await asyncio.sleep(interval)
