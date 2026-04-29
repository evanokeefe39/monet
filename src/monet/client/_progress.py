"""ProgressClient — data-plane telemetry queries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from monet.client._run import _build_agent_progress

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from monet.client._core import _ClientCore
    from monet.client._events import AgentProgress
    from monet.events import ProgressEvent


class ProgressClient:
    """Data-plane telemetry queries."""

    def __init__(self, core: _ClientCore) -> None:
        self._core = core

    async def get_progress_history(self, run_id: str) -> list[AgentProgress]:
        """Retrieve persisted progress events for a run."""
        import httpx

        headers: dict[str, str] = {}
        if self._core.api_key:
            headers["Authorization"] = f"Bearer {self._core.api_key}"
        url = self._core.url.rstrip("/") + f"/api/v1/runs/{run_id}/progress"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        events = data.get("events", []) if isinstance(data, dict) else []
        results: list[AgentProgress] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            progress = _build_agent_progress(run_id, ev)
            if progress is not None:
                results.append(progress)
        return results

    async def get_batch_progress(self, run_ids: list[str]) -> list[AgentProgress]:
        """Retrieve progress for multiple runs in one server round-trip."""
        if not run_ids:
            return []
        import httpx

        headers: dict[str, str] = {}
        if self._core.api_key:
            headers["Authorization"] = f"Bearer {self._core.api_key}"
        url = self._core.url.rstrip("/") + "/api/v1/progress"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url, headers=headers, params={"run_ids": ",".join(run_ids)}
            )
            resp.raise_for_status()
            data = resp.json()
        progress_map = data.get("progress", {}) if isinstance(data, dict) else {}
        results: list[AgentProgress] = []
        for rid, events in progress_map.items():
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                p = _build_agent_progress(str(rid), ev)
                if p is not None:
                    results.append(p)
        return results

    async def query_events(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[ProgressEvent]:
        """Fetch typed progress events for run_id from the data plane."""
        from monet.client._wire import query_progress_events

        return await query_progress_events(  # type: ignore[return-value]
            self._core.data_url,
            run_id,
            api_key=self._core.api_key,
            after=after,
            limit=limit,
        )

    async def subscribe_events(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[ProgressEvent]:
        """Stream typed progress events for run_id from the data plane."""
        from monet.client._wire import stream_progress_events

        async for event in stream_progress_events(
            self._core.data_url,
            run_id,
            api_key=self._core.api_key,
            after=after,
        ):
            yield event  # type: ignore[misc]
