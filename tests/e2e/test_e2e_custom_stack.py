"""E2E — fully custom stack proves CLI/client/server are graph-agnostic.

Starts ``monet dev`` in ``examples/custom-stack/`` — which ships
bespoke agents, a bespoke chat graph, and a bespoke pipeline — then
drives the server through :class:`MonetClient` only (no TUI). Each
test asserts a surface area that must remain usable when the user
replaces every default.

Gated behind ``MONET_E2E=1``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from monet._ports import STANDARD_DEV_PORT
from monet.client import MonetClient

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
CUSTOM_STACK_DIR = REPO_ROOT / "examples" / "custom-stack"
HEALTH_URL = f"http://localhost:{STANDARD_DEV_PORT}/health"
BOOT_TIMEOUT_SECONDS = 180.0
RUN_TIMEOUT_SECONDS = 300.0


def _wait_for_health(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(HEALTH_URL, timeout=2.0)
            if resp.status_code == 200:
                return
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            pass
        time.sleep(1.0)
    msg = f"monet dev did not become healthy within {timeout}s"
    raise RuntimeError(msg)


@pytest.fixture(scope="module")
def custom_stack_dev_server() -> Iterator[str]:
    """Start ``monet dev`` inside examples/custom-stack; yield its URL."""
    if not CUSTOM_STACK_DIR.exists():
        pytest.skip(f"custom-stack example missing at {CUSTOM_STACK_DIR}")
    monet_bin = shutil.which("monet")
    if monet_bin is None:
        pytest.skip("'monet' not on PATH")

    # monet dev's check_env() requires a .env. The custom-stack example
    # ships .env.example only so the repo stays secret-free. Materialise
    # .env from the example when absent so the e2e run is hermetic.
    env_target = CUSTOM_STACK_DIR / ".env"
    if not env_target.exists():
        env_src = CUSTOM_STACK_DIR / ".env.example"
        env_target.write_text(env_src.read_text(), encoding="utf-8")

    log_path = CUSTOM_STACK_DIR / ".monet" / "e2e-custom-stack.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("wb")
    proc = subprocess.Popen(
        [monet_bin, "dev"],
        cwd=CUSTOM_STACK_DIR,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_health(BOOT_TIMEOUT_SECONDS)
        yield f"http://localhost:{STANDARD_DEV_PORT}"
    finally:
        log_fh.close()
        subprocess.run(
            [monet_bin, "dev", "down"],
            cwd=CUSTOM_STACK_DIR,
            check=False,
            timeout=30,
        )
        # ``monet dev`` spawns ``aegra`` as a child which spawns uvicorn
        # workers. ``proc.terminate()`` only signals the top-level
        # process on Windows, leaving grandchildren to inherit the
        # listening socket. Use ``taskkill /T /F`` to kill the tree so
        # port 2026 is reliably released between test runs.
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                check=False,
                timeout=10,
                capture_output=True,
            )
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.e2e
async def test_custom_agents_registered_alongside_defaults(
    custom_stack_dev_server: str,
) -> None:
    """Only the four bespoke ``myco_*`` agents appear in the registry.

    Reference agents (planner/researcher/writer/qa/publisher) must NOT
    appear — ``import monet.agents`` is now deferred inside
    ``build_default_graph`` so it only runs when the default pipeline is
    compiled. Custom stacks that omit the default graph get a clean
    registry.
    """
    client = MonetClient(custom_stack_dev_server)
    caps = await client.list_capabilities()
    agent_ids = {c["agent_id"] for c in caps}
    expected = {
        "myco_planner",
        "myco_researcher",
        "myco_writer",
        "myco_conversationalist",
    }
    missing = expected - agent_ids
    assert not missing, f"custom agents absent from registry: {missing}"
    leaked = {aid for aid in agent_ids if not aid.startswith("myco_")}
    assert not leaked, f"reference agents leaked into custom-stack registry: {leaked}"


@pytest.mark.e2e
async def test_chat_respond_uses_custom_conversationalist(
    custom_stack_dev_server: str,
) -> None:
    """Plain chat turn routes through the bespoke conversationalist."""
    client = MonetClient(custom_stack_dev_server)
    thread_id = await client.chat.create_chat(name="e2e-custom-respond")
    chunks: list[str] = []
    async for chunk in client.chat.send_message(thread_id, "hello"):
        if isinstance(chunk, str):
            chunks.append(chunk)
    transcript = "".join(chunks)
    # Canned response from _stub_llm is prefixed "[conversationalist]".
    assert "[conversationalist]" in transcript, transcript


@pytest.mark.e2e
async def test_plan_approval_and_risk_review_round_trip(
    custom_stack_dev_server: str,
) -> None:
    """Two distinct bespoke interrupt envelopes both resume cleanly."""
    client = MonetClient(custom_stack_dev_server)
    thread_id = await client.chat.create_chat(name="e2e-custom-plan")

    # Drive to the plan_approval interrupt.
    async for _ in client.chat.send_message(thread_id, "/plan build a thing"):
        pass

    first = await client.chat.get_chat_interrupt(thread_id)
    assert first is not None, "graph did not pause for plan_approval"
    assert first["values"].get("kind") == "plan_approval", first["values"]

    # Resume: accept the plan.
    async for _ in client.chat.resume_chat(thread_id, {"decision": "accept"}):
        pass

    # Should now pause again on the distinct risk_review envelope.
    second = await client.chat.get_chat_interrupt(thread_id)
    assert second is not None, "graph did not pause for risk_review"
    assert second["values"].get("kind") == "risk_review", second["values"]

    # Resume: accept the risk posture; execute runs to completion.
    async for _ in client.chat.resume_chat(thread_id, {"tolerance": "accept"}):
        pass

    third = await client.chat.get_chat_interrupt(thread_id)
    assert third is None, f"graph still paused: {third}"

    history = await client.chat.get_chat_history(thread_id)
    joined = " ".join(str(m.get("content", "")) for m in history)
    assert "Executed" in joined, joined


@pytest.mark.e2e
async def test_plan_rejection_halts_without_risk_review(
    custom_stack_dev_server: str,
) -> None:
    """Rejecting the plan skips the risk_review interrupt entirely."""
    client = MonetClient(custom_stack_dev_server)
    thread_id = await client.chat.create_chat(name="e2e-custom-reject")

    async for _ in client.chat.send_message(thread_id, "/plan risky thing"):
        pass

    first = await client.chat.get_chat_interrupt(thread_id)
    assert first is not None

    async for _ in client.chat.resume_chat(thread_id, {"decision": "reject"}):
        pass

    after = await client.chat.get_chat_interrupt(thread_id)
    assert after is None, f"graph should terminate on reject, got {after}"


@pytest.mark.e2e
def test_custom_pipeline_runs_via_entrypoint(
    custom_stack_dev_server: str,
) -> None:
    """`monet run --graph custom_pipeline` drives the bespoke pipeline."""
    monet_bin = shutil.which("monet")
    assert monet_bin is not None

    env = os.environ.copy()
    env.setdefault("MONET_API_URL", custom_stack_dev_server)

    result = subprocess.run(
        [
            monet_bin,
            "run",
            "--graph",
            "custom_pipeline",
            "pipeline topic",
            "--output",
            "json",
        ],
        cwd=CUSTOM_STACK_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, (
        f"monet run exited {result.returncode}. stderr:\n{result.stderr}"
    )
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert events
    kinds = {
        ev.get("event") or ev.get("type")
        for ev in events
        if isinstance(ev.get("event") or ev.get("type"), str)
    }
    assert any(k and "run_complete" in k for k in kinds), (
        f"no run_complete from bespoke pipeline: {kinds}"
    )
