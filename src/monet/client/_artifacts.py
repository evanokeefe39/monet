"""ArtifactClient — artifact queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.client._core import _ClientCore


@dataclass
class ArtifactSummary:
    artifact_id: str
    summary: str
    kind: str
    agent_id: str = ""
    key: str = ""


class ArtifactClient:
    """Artifact queries."""

    def __init__(self, core: _ClientCore) -> None:
        self._core = core

    async def list_artifacts(
        self, *, thread_id: str, limit: int = 50
    ) -> list[ArtifactSummary]:
        """List artifacts for a thread, newest-first."""
        import httpx

        headers: dict[str, str] = {}
        if self._core.api_key:
            headers["Authorization"] = f"Bearer {self._core.api_key}"
        url = self._core.url.rstrip("/") + "/api/v1/artifacts"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                headers=headers,
                params={"thread_id": thread_id, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
        items = data.get("artifacts", []) if isinstance(data, dict) else []
        return [
            ArtifactSummary(
                artifact_id=item.get("artifact_id", ""),
                summary=item.get("summary", ""),
                kind=item.get("content_type", ""),
                agent_id=item.get("agent_id", "") or "",
                key=item.get("key", "") or "",
            )
            for item in items
            if isinstance(item, dict)
        ]

    async def count_artifacts_per_thread(self, thread_ids: list[str]) -> dict[str, int]:
        """Return artifact counts keyed by thread_id — one server round trip."""
        if not thread_ids:
            return {}
        import httpx

        headers: dict[str, str] = {}
        if self._core.api_key:
            headers["Authorization"] = f"Bearer {self._core.api_key}"
        url = self._core.url.rstrip("/") + "/api/v1/artifacts/counts"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                headers=headers,
                params={"thread_ids": ",".join(thread_ids)},
            )
            resp.raise_for_status()
            data = resp.json()
        return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
