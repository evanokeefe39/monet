"""High-level client for interacting with a monet LangGraph server.

:class:`MonetClient` is graph-agnostic: it drives any graph declared in
``monet.toml [entrypoints]``, streams typed core events, and exposes
generic HITL resume via :meth:`MonetClient.resume`. The default
``entry → planning → execution`` pipeline ships as a single compound
graph (``monet.orchestration.build_default_graph``) so a
``client.run("default", ...)`` call drives it end-to-end on one
thread, with native LangGraph ``interrupt()`` for HITL.

Typical library usage::

    from monet.client import MonetClient
    from monet.client._wire import task_input

    client = MonetClient("http://localhost:2026")
    async for event in client.run("default", task_input("topic", "")):
        print(event)
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import TYPE_CHECKING, Any, cast

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
    Capability,
    ChatSummary,
    Field,
    FieldOption,
    FieldType,
    Form,
    Interrupt,
    NodeUpdate,
    PendingDecision,
    RunComplete,
    RunDetail,
    RunEvent,
    RunFailed,
    RunStarted,
    RunSummary,
    SignalEmitted,
)
from monet.client._run_state import _RunStore
from monet.client._wire import (
    MONET_GRAPH_KEY,
    MONET_RUN_ID_KEY,
    create_thread,
    get_state_values,
    make_client,
    stream_run,
    task_input,
)
from monet.client.chat import ChatClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph_sdk.client import LangGraphClient

_log = logging.getLogger("monet.client")


def _extract_interrupt_payload(state: Any) -> dict[str, Any]:
    """Pull the first interrupt payload off a LangGraph state snapshot.

    The payload lives on ``state.tasks[0].interrupts[0].value`` in the
    LangGraph SDK response; it is not mirrored into
    ``state.values["__interrupt__"]``. Tolerates both mapping-style and
    attribute-style access because the SDK returns plain dicts for some
    endpoints and pydantic-esque objects for others.
    """

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


__all__ = [
    "AgentProgress",
    "AlreadyResolved",
    "AmbiguousInterrupt",
    "Capability",
    "ChatClient",
    "ChatSummary",
    "Field",
    "FieldOption",
    "FieldType",
    "Form",
    "GraphNotInvocable",
    "Interrupt",
    "InterruptTagMismatch",
    "MonetClient",
    "MonetClientError",
    "NodeUpdate",
    "PendingDecision",
    "RunComplete",
    "RunDetail",
    "RunEvent",
    "RunFailed",
    "RunNotInterrupted",
    "RunStarted",
    "RunSummary",
    "SignalEmitted",
    "make_client",
]


def _build_agent_progress(run_id: str, data: dict[str, Any]) -> AgentProgress | None:
    """Convert a custom-stream wire dict into an :class:`AgentProgress`.

    Returns ``None`` when the dict does not carry an ``agent`` field —
    callers should skip such payloads rather than emit a malformed event.
    """
    agent = data.get("agent", "")
    if not agent:
        return None
    return AgentProgress(
        run_id=run_id,
        agent_id=agent,
        status=data.get("status", ""),
        reasons=data.get("reasons", ""),
    )


def _build_signal_emitted(run_id: str, data: dict[str, Any]) -> SignalEmitted | None:
    """Convert a custom-stream wire dict into a :class:`SignalEmitted`.

    Monet's ``emit_signal`` writer produces dicts with at least
    ``{"signal_type": ..., "agent": ..., ...}``. Returns ``None`` when
    the dict doesn't look like a signal payload.
    """
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


class MonetClient:
    """Graph-agnostic client for a monet LangGraph server.

    Three groups of operations:

    - **Run lifecycle** — :meth:`run` drives any declared graph and
      yields core events; :meth:`list_runs` / :meth:`get_run` inspect
      history.
    - **HITL** — :meth:`resume` dispatches a resume payload to a
      paused interrupt; :meth:`abort` terminates a run.
    - **Chat** — :attr:`chat` exposes all chat-specific operations via
      :class:`~monet.client.chat.ChatClient` (create, list, send, resume,
      interrupt, history). Resolved from ``monet.toml [graphs]``.

    Interrupts surface as the generic :class:`Interrupt` event and are
    answered with :meth:`resume`. Form-schema convention (see
    :class:`Form` / :class:`Field`) lets any consumer render the pause
    uniformly without pipeline-specific verbs.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        api_key: str | None = None,
        graph_ids: dict[str, str] | None = None,
    ) -> None:
        from monet.client.chat import ChatClient
        from monet.config import ClientConfig, load_entrypoints, load_graph_roles

        cfg = ClientConfig.load()
        resolved_url = url if url is not None else cfg.server_url
        resolved_key = api_key if api_key is not None else cfg.api_key
        self._url = resolved_url
        self._api_key = resolved_key
        self._client: LangGraphClient = make_client(resolved_url, api_key=resolved_key)
        self._store = _RunStore()
        self._entrypoints = load_entrypoints()
        if graph_ids is not None:
            self._graph_roles = dict(graph_ids)
        else:
            self._graph_roles = load_graph_roles()
        self.chat = ChatClient(
            self._client,
            chat_graph_id=self._graph_roles.get("chat", "chat"),
        )

    # ── Run lifecycle ───────────────────────────────────────────

    async def run(
        self,
        graph_id: str,
        input: dict[str, Any] | str | None = None,
        *,
        run_id: str | None = None,
    ) -> AsyncIterator[RunEvent]:
        """Drive one graph and yield typed core events.

        Args:
            graph_id: A graph declared in ``monet.toml [entrypoints]``
                (by ``graph`` value). Raises :class:`GraphNotInvocable`
                if not declared.
            input: Initial state dict. If a string is passed, it is
                wrapped by :func:`task_input`. If ``None``, an empty
                dict is used (for graphs that take no input).
            run_id: Optional run identifier. Auto-generated if omitted.

        Yields:
            :class:`RunStarted`, then :class:`NodeUpdate` /
            :class:`AgentProgress` / :class:`SignalEmitted` chunks as
            they arrive, then either :class:`Interrupt` (run paused) or
            :class:`RunComplete` / :class:`RunFailed`.
        """
        declared_graphs = {ep["graph"] for ep in self._entrypoints.values()}
        if graph_id not in declared_graphs:
            raise GraphNotInvocable(graph_id, sorted(declared_graphs))

        rid = run_id or secrets.token_hex(4)
        if isinstance(input, str):
            input = task_input(input, rid)

        thread = await create_thread(
            self._client,
            metadata={MONET_RUN_ID_KEY: rid, MONET_GRAPH_KEY: graph_id},
        )
        self._store.put_thread(rid, graph_id, thread)
        yield RunStarted(run_id=rid, graph_id=graph_id, thread_id=thread)

        try:
            async for mode, data in stream_run(
                self._client, thread, graph_id, input=input or {}
            ):
                if mode == "error":
                    yield RunFailed(run_id=rid, error=str(data))
                    return
                if mode == "custom" and isinstance(data, dict):
                    signal = _build_signal_emitted(rid, data)
                    if signal is not None:
                        yield signal
                        continue
                    progress = _build_agent_progress(rid, data)
                    if progress is not None:
                        yield progress
                        continue
                elif mode == "updates" and isinstance(data, dict):
                    for node_name, update in data.items():
                        if isinstance(update, dict):
                            yield NodeUpdate(run_id=rid, node=node_name, update=update)

            values, nxt = await get_state_values(self._client, thread)
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

    # ── HITL ────────────────────────────────────────────────────

    async def resume(
        self,
        run_id: str,
        tag: str,
        payload: dict[str, Any],
    ) -> None:
        """Resume a paused interrupt.

        Validates that the run is actually paused at *tag* before
        dispatching. Raises :class:`RunNotInterrupted`,
        :class:`AlreadyResolved`, :class:`AmbiguousInterrupt`, or
        :class:`InterruptTagMismatch` on mismatch.

        Args:
            run_id: The run to resume.
            tag: The interrupt node name (must match ``Interrupt.tag``).
            payload: Dict forwarded to the graph as ``Command(resume=payload)``.
        """
        thread, graph_id = await self._find_interrupted_thread(run_id)
        _, nxt = await get_state_values(self._client, thread)
        if not nxt:
            raise AlreadyResolved(run_id)
        if len(nxt) > 1:
            raise AmbiguousInterrupt(run_id, list(nxt))
        if nxt[0] != tag:
            raise InterruptTagMismatch(run_id, expected=nxt[0], got=tag)

        # Checkpointer exposes `next` the moment the graph hits interrupt(),
        # but Aegra's resume validator rejects until ThreadORM.status is
        # committed to "interrupted" via finalize_run. Poll briefly so the
        # failure mode is a deterministic timeout, not a 400 race.
        await self._await_interrupted_status(thread)

        _log.info(
            "resume",
            extra={"run_id": run_id, "tag": tag, "payload_keys": list(payload)},
        )
        async for mode, data in stream_run(
            self._client,
            thread,
            graph_id,
            command={"resume": payload},
        ):
            if mode == "error":
                raise RuntimeError(f"server error: {data}")

    async def abort(self, run_id: str) -> None:
        """Abort a paused run — resumes with a canonical abort payload.

        Finds the interrupted thread and dispatches
        ``{"resume": {"action": "abort"}}``. Graphs that don't consume
        that shape will simply continue past the interrupt.
        """
        thread, graph_id = await self._find_interrupted_thread(run_id)
        async for mode, data in stream_run(
            self._client,
            thread,
            graph_id,
            command={"resume": {"action": "abort"}},
        ):
            if mode == "error":
                raise RuntimeError(f"server error: {data}")

    # ── Queries ─────────────────────────────────────────────────

    async def invoke_agent(
        self,
        agent_id: str,
        command: str,
        *,
        task: str = "",
        context: list[dict[str, Any]] | None = None,
        skills: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run a single ``agent_id:command`` invocation on the server.

        Bypasses graphs entirely — good for direct calls (``monet run
        <agent>:<command>``, chat REPL slash commands). The server
        dispatches via its configured queue and returns the resulting
        ``AgentResult`` as a dict.
        """
        import httpx

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        url = self._url.rstrip("/") + f"/api/v1/agents/{agent_id}/{command}/invoke"
        payload: dict[str, Any] = {"task": task}
        if context is not None:
            payload["context"] = context
        if skills is not None:
            payload["skills"] = skills
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=10.0)
        ) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return dict(data) if isinstance(data, dict) else {}

    async def list_capabilities(self) -> list[Capability]:
        """List every ``(agent_id, command)`` declared on the server.

        Drives dynamic slash-command discovery in ``monet chat`` and
        resolves the target for ``monet run <agent>:<command>`` direct
        invocation. Returns an empty list if the server has no manifest
        entries.
        """
        import httpx

        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        url = self._url.rstrip("/") + "/api/v1/agents"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, list):
            return []
        return [cast("Capability", item) for item in data if isinstance(item, dict)]

    async def slash_commands(self) -> list[str]:
        """Return the client-visible slash-command vocabulary.

        Mirrors :meth:`monet.core.manifest.AgentManifest.slash_commands`
        from the server side: the framework-reserved prefixes (``/plan``)
        followed by ``/<agent_id>:<command>`` for each declared
        capability. Feeds the Textual chat REPL's completion suggester
        and command palette.
        """
        from monet.core.manifest import RESERVED_SLASH

        out: list[str] = list(RESERVED_SLASH)
        seen: set[str] = set(out)
        for cap in await self.list_capabilities():
            cmd = f"/{cap['agent_id']}:{cap['command']}"
            if cmd not in seen:
                out.append(cmd)
                seen.add(cmd)
        return out

    async def list_runs(self, *, limit: int = 20) -> list[RunSummary]:
        """List recent runs with status and completed stages.

        Groups threads by ``monet_run_id`` metadata. The first-seen
        graph_id per run (in creation order, newest first) is treated
        as the run's head for timing purposes.
        """
        threads = await self._client.threads.search(
            metadata={MONET_RUN_ID_KEY: None},
            limit=limit * 4,  # overscan; dedupe by run_id below
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
            head = ts[-1]  # newest
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
        """Merge all threads for *run_id* into a generic :class:`RunDetail`."""
        threads = await self._client.threads.search(
            metadata={MONET_RUN_ID_KEY: run_id},
            limit=20,
        )
        # Order threads by creation — earliest first — so later-stage
        # keys win on collision.
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

            values, nxt = await get_state_values(self._client, tid)
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
        """List runs currently waiting for human input."""
        threads = await self._client.threads.search(
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
            _, nxt = await get_state_values(self._client, tid)
            tag = nxt[0] if nxt else ""
            decisions.append(PendingDecision(run_id=rid, decision_type=tag, summary=""))
        return decisions

    async def list_graphs(self) -> list[str]:
        """Return graph IDs available on the connected server."""
        assistants = await self._client.assistants.search(limit=100)
        graph_ids: list[str] = []
        seen: set[str] = set()
        for a in assistants:
            gid = a.get("graph_id", "")
            if gid and gid not in seen:
                seen.add(gid)
                graph_ids.append(gid)
        return sorted(graph_ids)

    # ── Private helpers ─────────────────────────────────────────

    async def _find_interrupted_thread(self, run_id: str) -> tuple[str, str]:
        """Return ``(thread_id, graph_id)`` for the currently paused thread."""
        cached = self._store.threads_for(run_id)
        if cached:
            for graph_id, thread_id in cached.items():
                _, nxt = await get_state_values(self._client, thread_id)
                if nxt:
                    return thread_id, graph_id

        threads = await self._client.threads.search(
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

    async def _await_interrupted_status(
        self,
        thread_id: str,
        *,
        timeout: float = 3.0,
        interval: float = 0.05,
    ) -> None:
        """Poll ``thread.status`` until ``"interrupted"`` or timeout.

        Aegra's resume validator rejects unless the thread row is
        committed to ``status="interrupted"``. That commit runs after
        the graph stream exits but before the broker "end" event; in
        practice scheduling can leave a brief window where the client
        has observed the interrupt but the DB has not.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            thread = await self._client.threads.get(thread_id)
            if thread.get("status") == "interrupted":
                return
            if loop.time() >= deadline:
                raise MonetClientError(
                    f"thread {thread_id!r} did not reach "
                    f"'interrupted' status within {timeout}s"
                )
            await asyncio.sleep(interval)
