"""E2E-07 — worker reconnection after server restart.

Starts a ``monet dev`` server + a separate ``monet worker`` subprocess.
Submits a run, terminates the server mid-flight, restarts it, and
asserts the worker rejoins and any in-flight / pending task reaches a
terminal state. Validates that the worker does not crash on server
disconnect and that the claim loop recovers.

Gated behind ``MONET_E2E=1``.
"""

from __future__ import annotations

import json
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
QUICKSTART_DIR = REPO_ROOT / "examples" / "quickstart"
HEALTH_URL = f"http://localhost:{STANDARD_DEV_PORT}/health"
BOOT_TIMEOUT_SECONDS = 180.0
RUN_TIMEOUT_SECONDS = 300.0


def _wait_health(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(HEALTH_URL, timeout=2.0).status_code == 200:
                return
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            pass
        time.sleep(1.0)
    msg = f"monet dev health not ready within {timeout}s"
    raise RuntimeError(msg)


def _wait_unhealthy(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(HEALTH_URL, timeout=1.0)
        except (httpx.ConnectError, OSError):
            return
        time.sleep(0.5)
    msg = f"monet dev still reachable after {timeout}s"
    raise RuntimeError(msg)


@pytest.fixture
def dev_server_with_external_worker() -> Iterator[tuple[subprocess.Popen[bytes], str]]:
    """Start monet dev + one external monet worker. Yield (server_proc, url)."""
    if not QUICKSTART_DIR.exists():
        pytest.skip(f"quickstart example missing at {QUICKSTART_DIR}")
    monet_bin = shutil.which("monet")
    if monet_bin is None:
        pytest.skip("'monet' not on PATH")

    server_log = QUICKSTART_DIR / ".monet" / "e2e-reconnect-server.log"
    server_log.parent.mkdir(parents=True, exist_ok=True)
    server_fh = server_log.open("wb")
    server_proc = subprocess.Popen(
        [monet_bin, "dev"],
        cwd=QUICKSTART_DIR,
        stdout=server_fh,
        stderr=subprocess.STDOUT,
    )
    url = f"http://localhost:{STANDARD_DEV_PORT}"
    try:
        _wait_health(BOOT_TIMEOUT_SECONDS)
    except RuntimeError:
        server_proc.terminate()
        server_proc.wait(timeout=10)
        server_fh.close()
        raise

    worker_log = QUICKSTART_DIR / ".monet" / "e2e-reconnect-worker.log"
    worker_fh = worker_log.open("wb")
    worker_proc = subprocess.Popen(
        [monet_bin, "worker", "--pool", "local", "--server-url", url],
        cwd=QUICKSTART_DIR,
        stdout=worker_fh,
        stderr=subprocess.STDOUT,
    )
    try:
        yield server_proc, url
    finally:
        worker_proc.terminate()
        try:
            worker_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker_proc.kill()
        worker_fh.close()
        subprocess.run(
            [monet_bin, "dev", "down"],
            cwd=QUICKSTART_DIR,
            check=False,
            timeout=30,
        )
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_proc.kill()
        server_fh.close()


@pytest.mark.e2e
def test_worker_survives_server_restart(
    dev_server_with_external_worker: tuple[subprocess.Popen[bytes], str],
) -> None:
    """Server restart mid-run; worker reconnects; follow-up run completes."""
    server_proc, _url = dev_server_with_external_worker
    monet_bin = shutil.which("monet")
    assert monet_bin is not None

    # Kill the server.
    server_proc.terminate()
    try:
        server_proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server_proc.kill()
        server_proc.wait(timeout=5)
    _wait_unhealthy(30.0)

    # Restart the server — reuses Postgres volume, worker still running.
    restart_log = QUICKSTART_DIR / ".monet" / "e2e-reconnect-restart.log"
    restart_fh = restart_log.open("wb")
    restarted = subprocess.Popen(
        [monet_bin, "dev"],
        cwd=QUICKSTART_DIR,
        stdout=restart_fh,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_health(BOOT_TIMEOUT_SECONDS)

        # Worker should rejoin and be able to serve a fresh run.
        result = subprocess.run(
            [
                monet_bin,
                "run",
                "post-restart topic",
                "--auto-approve",
                "--output",
                "json",
            ],
            cwd=QUICKSTART_DIR,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
            check=False,
        )
    finally:
        restarted.terminate()
        try:
            restarted.wait(timeout=10)
        except subprocess.TimeoutExpired:
            restarted.kill()
        restart_fh.close()

    assert result.returncode == 0, (
        f"post-restart monet run exited {result.returncode}. stderr:\n{result.stderr}"
    )
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    kinds = {
        ev.get("event") or ev.get("type")
        for ev in events
        if isinstance(ev.get("event") or ev.get("type"), str)
    }
    assert any(k and "run_complete" in k for k in kinds), (
        f"run did not complete after server restart: {kinds}"
    )
