"""Background status-bar and slash-command refresh mixin."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from monet.cli.chat._slash import RegistrySuggester
    from monet.client import MonetClient

from monet.cli.chat._constants import TUI_COMMANDS


class IndicatorMixin:
    """Mixin for background indicator and slash-command refresh.

    Host must supply: ``_client``, ``_server_slash_commands``,
    ``slash_commands``, ``slash_descriptions``, ``_suggester``,
    ``_status_bar``, ``thread_id``, ``run_worker``,
    ``_combined_slash_commands``.
    """

    _client: MonetClient
    _server_slash_commands: list[str]
    slash_commands: list[str]
    slash_descriptions: dict[str, str]
    _suggester: RegistrySuggester
    thread_id: Any

    # Provided by Textual App / ChatApp.
    run_worker: Any

    def _combined_slash_commands(self) -> list[str]:
        raise NotImplementedError

    async def _refresh_slash_commands(self) -> None:
        try:
            commands = await self._client.slash_commands()
        except Exception:
            return
        self._server_slash_commands = commands
        self.slash_commands = self._combined_slash_commands()
        self._suggester.update(self.slash_commands)
        descriptions: dict[str, str] = dict(TUI_COMMANDS)
        try:
            caps = await self._client.list_capabilities()
        except Exception:
            caps = []
        for cap in caps:
            agent_id = str(cap.get("agent_id") or "")
            command = str(cap.get("command") or "")
            desc = str(cap.get("description") or "").strip()
            if agent_id and command and desc:
                descriptions[f"/{agent_id}:{command}"] = desc
        self.slash_descriptions = descriptions

    def _refresh_indicator(self) -> None:
        self.run_worker(self._refresh_indicator_async(), exclusive=False)

    async def _refresh_indicator_async(self) -> None:
        async def _get_agents() -> int:
            try:
                return len(await self._client.list_capabilities())
            except Exception:
                return 0

        async def _get_artifacts() -> int:
            if not self.thread_id:
                return 0
            try:
                return len(await self._client.list_artifacts(thread_id=self.thread_id))
            except Exception:
                return 0

        async def _get_runs() -> int:
            if not self.thread_id:
                return 0
            try:
                return await self._client.chat.count_thread_runs(self.thread_id)
            except Exception:
                return 0

        agents, artifacts, runs = await asyncio.gather(
            _get_agents(), _get_artifacts(), _get_runs()
        )
        self._status_bar.update_segments(agents=agents, artifacts=artifacts, runs=runs)  # type: ignore[attr-defined]
