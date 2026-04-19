"""S1 monolith loopback: in-process worker heartbeats through the same
ASGI app the server serves.

Exercises the full ``_aegra_routes.app`` lifespan so the reference
agents registered by ``import monet.agents`` land in the
:class:`CapabilityIndex` within one claim-poll cycle. Closes the "chat
shows 0 agents on boot" regression.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:
    import pytest

_HEADERS = {"Authorization": "Bearer test-key"}


async def test_monolith_loopback_registers_reference_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    monkeypatch.setenv("MONET_QUEUE_BACKEND", "memory")

    # Import lazily so the monkeypatched env is in place before
    # server_bootstrap runs its module-level config validation.
    from monet.server import _aegra_routes

    app = _aegra_routes.app
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/agents", headers=_HEADERS)
    assert resp.status_code == 200
    caps = resp.json()
    agent_ids = {c["agent_id"] for c in caps}
    # Reference agent roster. Assertion is permissive on pool so future
    # reassignment does not break the test.
    assert {"planner", "researcher", "writer", "qa", "publisher"} <= agent_ids
    # Monolith worker advertises for pool="local".
    for c in caps:
        assert c["pool"] == "local"
        assert c["worker_ids"] == ["monolith-0"]
