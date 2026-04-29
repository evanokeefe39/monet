"""CapabilitiesClient — agent discovery and direct invocation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from monet.client._core import _ClientCore
    from monet.client._events import Capability


class CapabilitiesClient:
    """Agent discovery and direct invocation."""

    def __init__(self, core: _ClientCore) -> None:
        self._core = core

    async def list_capabilities(self) -> list[Capability]:
        """List every (agent_id, command) declared on the server."""
        import httpx

        headers: dict[str, str] = {}
        if self._core.api_key:
            headers["Authorization"] = f"Bearer {self._core.api_key}"
        url = self._core.url.rstrip("/") + "/api/v1/agents"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, list):
            return []
        return [cast("Capability", item) for item in data if isinstance(item, dict)]

    async def slash_commands(self) -> list[str]:
        """Return the client-visible slash-command vocabulary."""
        from monet.server._capabilities import RESERVED_SLASH

        out: list[str] = list(RESERVED_SLASH)
        seen: set[str] = set(out)
        for cap in await self.list_capabilities():
            cmd = f"/{cap['agent_id']}:{cap['command']}"
            if cmd not in seen:
                out.append(cmd)
                seen.add(cmd)
        return out

    async def invoke_agent(
        self,
        agent_id: str,
        command: str,
        *,
        task: str = "",
        context: list[dict[str, Any]] | None = None,
        skills: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run a single agent_id:command invocation on the server."""
        import httpx

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._core.api_key:
            headers["Authorization"] = f"Bearer {self._core.api_key}"
        url = self._core.url.rstrip("/") + f"/api/v1/agents/{agent_id}/{command}/invoke"
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
