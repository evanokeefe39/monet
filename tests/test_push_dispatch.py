"""Push-pool orchestrator dispatcher.

Covers the orchestrator side of push dispatch: ``invoke_agent`` against
a ``type="push"`` pool POSTs to the webhook URL with the HMAC token and
the dispatch secret, then resolves ``wait_completion`` when the callback
POSTs back to ``/complete``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from httpx import Response

if TYPE_CHECKING:
    from pathlib import Path

    import respx

from monet.core.registry import AgentRegistry
from monet.orchestration._invoke import (
    close_dispatch_client,
    configure_queue,
    invoke_agent,
)
from monet.queue import InMemoryTaskQueue
from monet.server._auth import task_hmac
from monet.types import AgentResult

API_KEY = "push-api-key"
DISPATCH_SECRET = "dispatch-shh"
WEBHOOK_URL = "http://worker.example/dispatch"


@pytest.fixture(autouse=True)
async def _env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Any:
    # Write a monet.toml with a push pool so load_config picks it up.
    monet_toml = tmp_path / "monet.toml"
    monet_toml.write_text(
        f"""
[pools.cloud]
type = "push"
url = "{WEBHOOK_URL}"
dispatch_secret = "{DISPATCH_SECRET}"
"""
    )
    monkeypatch.setenv("MONET_CONFIG_PATH", str(monet_toml))
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    monkeypatch.setenv("MONET_SERVER_URL", "http://orchestrator.example")

    # Wire up an in-memory queue + register the push agent in the manifest
    # so invoke_agent routes to the cloud pool.
    from monet.agent_manifest import configure_agent_manifest
    from monet.core.manifest import default_manifest

    queue = InMemoryTaskQueue()
    configure_queue(queue)
    configure_agent_manifest(default_manifest)
    default_manifest.declare(
        "cloud-agent", "fast", description="", pool="cloud", worker_id="w"
    )
    registry = AgentRegistry()
    yield queue, registry
    await close_dispatch_client()
    configure_queue(None)


@pytest.mark.respx
async def test_push_dispatch_posts_webhook_and_resolves_on_complete(
    _env: Any, respx_mock: respx.MockRouter
) -> None:
    queue, _registry = _env
    captured_tokens: list[str] = []
    captured_task_ids: list[str] = []

    async def _on_dispatch(request: Any) -> Response:
        body = request.content.decode()
        import json

        envelope = json.loads(body)
        captured_tokens.append(envelope["token"])
        captured_task_ids.append(envelope["task_id"])
        # Simulate the cloud worker: POST back to /complete.
        task_id = envelope["task_id"]
        result = AgentResult(success=True, output="cloud-run", trace_id="t", run_id="r")
        await queue.complete(task_id, result)
        return Response(202, json={"status": "accepted"})

    route = respx_mock.post(WEBHOOK_URL).mock(side_effect=_on_dispatch)

    result = await invoke_agent("cloud-agent", "fast", task="hello")
    assert result.success is True
    assert result.output == "cloud-run"
    assert route.called
    assert captured_task_ids, "dispatch envelope must include task_id"
    # Token is HMAC(MONET_API_KEY, task_id).
    expected_token = task_hmac(API_KEY, captured_task_ids[0])
    assert captured_tokens[0] == expected_token


@pytest.mark.respx
async def test_push_dispatch_sends_dispatch_secret(
    _env: Any, respx_mock: respx.MockRouter
) -> None:
    queue, _registry = _env
    captured_auth: list[str | None] = []

    async def _on_dispatch(request: Any) -> Response:
        captured_auth.append(request.headers.get("authorization"))
        import json

        task_id = json.loads(request.content.decode())["task_id"]
        await queue.complete(
            task_id,
            AgentResult(success=True, output="ok", trace_id="t", run_id="r"),
        )
        return Response(202, json={"status": "accepted"})

    respx_mock.post(WEBHOOK_URL).mock(side_effect=_on_dispatch)
    await invoke_agent("cloud-agent", "fast", task="x")
    assert captured_auth[0] == f"Bearer {DISPATCH_SECRET}"


@pytest.mark.respx
async def test_push_dispatch_raises_on_5xx(
    _env: Any, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(WEBHOOK_URL).mock(return_value=Response(503, text="gateway down"))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        await invoke_agent("cloud-agent", "fast", task="hello")
