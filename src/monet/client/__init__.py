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

from typing import TYPE_CHECKING, Any

from monet.client._artifacts import ArtifactClient, ArtifactSummary
from monet.client._capabilities import CapabilitiesClient
from monet.client._core import _ClientCore
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
from monet.client._progress import ProgressClient
from monet.client._run import RunClient, _build_agent_progress
from monet.client._run_state import _RunStore
from monet.client._wire import make_client
from monet.client.chat import ChatClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from monet.events import ProgressEvent

__all__ = [
    "AgentProgress",
    "AlreadyResolved",
    "AmbiguousInterrupt",
    "ArtifactSummary",
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
    "_build_agent_progress",
    "make_client",
]


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
        data_plane_url: str | None = None,
        graph_ids: dict[str, str] | None = None,
    ) -> None:
        from monet.client.chat import ChatClient
        from monet.config import ClientConfig, load_entrypoints, load_graph_roles

        cfg = ClientConfig.load()
        resolved_url = url if url is not None else cfg.server_url
        resolved_key = api_key if api_key is not None else cfg.api_key
        resolved_data_url = (
            data_plane_url if data_plane_url is not None else cfg.data_plane_url
        )
        resolved_data_url = resolved_data_url or resolved_url

        entrypoints = load_entrypoints()
        graph_roles = dict(graph_ids) if graph_ids is not None else load_graph_roles()

        core = _ClientCore(
            url=resolved_url,
            api_key=resolved_key,
            data_url=resolved_data_url,
            client=make_client(resolved_url, api_key=resolved_key),
            store=_RunStore(),
            entrypoints=entrypoints,
            graph_roles=graph_roles,
        )
        self._core = core
        self._runs = RunClient(core)
        self._capabilities = CapabilitiesClient(core)
        self._artifacts = ArtifactClient(core)
        self._progress = ProgressClient(core)
        self.chat = ChatClient(
            core.client,
            chat_graph_id=graph_roles.get("chat", "chat"),
            base_url=resolved_url,
            api_key=resolved_key,
        )

    # ── Run lifecycle ────────────────────────────────────────────

    async def run(
        self,
        graph_id: str,
        input: dict[str, Any] | str | None = None,
        *,
        run_id: str | None = None,
    ) -> AsyncIterator[RunEvent]:
        async for event in self._runs.run(graph_id, input, run_id=run_id):
            yield event

    async def resume(self, run_id: str, tag: str, payload: dict[str, Any]) -> None:
        return await self._runs.resume(run_id, tag, payload)

    async def abort(self, run_id: str) -> None:
        return await self._runs.abort(run_id)

    async def list_runs(self, *, limit: int = 20) -> list[RunSummary]:
        return await self._runs.list_runs(limit=limit)

    async def get_run(self, run_id: str) -> RunDetail:
        return await self._runs.get_run(run_id)

    async def list_pending(self) -> list[PendingDecision]:
        return await self._runs.list_pending()

    async def list_graphs(self) -> list[str]:
        return await self._runs.list_graphs()

    # ── Capabilities ─────────────────────────────────────────────

    async def list_capabilities(self) -> list[Capability]:
        return await self._capabilities.list_capabilities()

    async def slash_commands(self) -> list[str]:
        return await self._capabilities.slash_commands()

    async def invoke_agent(
        self,
        agent_id: str,
        command: str,
        *,
        task: str = "",
        context: list[dict[str, Any]] | None = None,
        skills: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self._capabilities.invoke_agent(
            agent_id, command, task=task, context=context, skills=skills
        )

    # ── Artifacts ────────────────────────────────────────────────

    async def list_artifacts(
        self, *, thread_id: str, limit: int = 50
    ) -> list[ArtifactSummary]:
        return await self._artifacts.list_artifacts(thread_id=thread_id, limit=limit)

    async def count_artifacts_per_thread(self, thread_ids: list[str]) -> dict[str, int]:
        return await self._artifacts.count_artifacts_per_thread(thread_ids)

    # ── Progress / telemetry ─────────────────────────────────────

    async def get_progress_history(self, run_id: str) -> list[AgentProgress]:
        return await self._progress.get_progress_history(run_id)

    async def get_batch_progress(self, run_ids: list[str]) -> list[AgentProgress]:
        return await self._progress.get_batch_progress(run_ids)

    async def get_thread_progress(self, thread_id: str) -> list[AgentProgress]:
        """Fetch progress history for all completed runs on a thread."""
        runs = await self.chat.list_thread_runs(thread_id)
        run_ids = [r.run_id for r in runs if r.status != "interrupted"]
        return await self._progress.get_batch_progress(run_ids)

    async def query_events(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[ProgressEvent]:
        return await self._progress.query_events(run_id, after=after, limit=limit)

    async def subscribe_events(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[ProgressEvent]:
        async for event in self._progress.subscribe_events(run_id, after=after):
            yield event  # type: ignore[misc]

    # ── Compat accessors (used by tests) ─────────────────────────

    @property
    def _url(self) -> str:
        return self._core.url

    @property
    def _data_url(self) -> str:
        return self._core.data_url
