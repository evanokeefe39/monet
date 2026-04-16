"""E2E-06 — custom graph registration via aegra.json + monet run --graph.

Starts ``monet dev`` in the ``examples/custom-graph`` directory — which
ships its own ``aegra.json`` exposing a ``review`` graph + a
``monet.toml`` declaring it as an invocable entrypoint — and drives the
custom graph via ``monet run --graph review "topic"``. Asserts the run
streams at least one event and terminates cleanly.

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

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
CUSTOM_GRAPH_DIR = REPO_ROOT / "examples" / "custom-graph"
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


@pytest.fixture
def custom_graph_dev_server() -> Iterator[str]:
    """Start ``monet dev`` inside examples/custom-graph; yield its URL."""
    if not CUSTOM_GRAPH_DIR.exists():
        pytest.skip(f"custom-graph example missing at {CUSTOM_GRAPH_DIR}")
    monet_bin = shutil.which("monet")
    if monet_bin is None:
        pytest.skip("'monet' not on PATH")

    log_path = CUSTOM_GRAPH_DIR / ".monet" / "e2e-custom-graph.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("wb")
    proc = subprocess.Popen(
        [monet_bin, "dev"],
        cwd=CUSTOM_GRAPH_DIR,
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
            cwd=CUSTOM_GRAPH_DIR,
            check=False,
            timeout=30,
        )
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.e2e
def test_custom_graph_runs_via_entrypoint(
    custom_graph_dev_server: str,
) -> None:
    """`monet run --graph review` drives the aegra.json-registered graph."""
    monet_bin = shutil.which("monet")
    assert monet_bin is not None

    env = os.environ.copy()
    env.setdefault("MONET_API_URL", custom_graph_dev_server)

    result = subprocess.run(
        [
            monet_bin,
            "run",
            "--graph",
            "review",
            "short topic for custom pipeline",
            "--output",
            "json",
        ],
        cwd=CUSTOM_GRAPH_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, (
        f"monet run --graph review exited {result.returncode}. stderr:\n{result.stderr}"
    )
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert events, "custom graph produced no events"
    kinds = {
        ev.get("event") or ev.get("type")
        for ev in events
        if isinstance(ev.get("event") or ev.get("type"), str)
    }
    assert any(k and "run_complete" in k for k in kinds), (
        f"no run_complete in events from custom graph: {kinds}"
    )
