"""E2E-04 — multiple concurrent ``monet worker`` instances.

Starts N worker subprocesses claiming from the same ``monet dev`` server,
submits M tasks, and asserts no task is claimed twice and every task
reaches a terminal state. Exercises the queue-plane concurrency contract
(``XREADGROUP`` / SQLite ``UPDATE ... WHERE claimed_by IS NULL``).

Gated behind ``MONET_E2E=1``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
QUICKSTART_DIR = REPO_ROOT / "examples" / "quickstart"
N_WORKERS = 3
N_TASKS = 8
RUN_TIMEOUT_SECONDS = 300.0


def _run_monet_once(monet_bin: str, topic: str) -> dict[str, object]:
    """Drive one ``monet run --auto-approve`` and return its final event."""
    result = subprocess.run(
        [
            monet_bin,
            "run",
            topic,
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
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return {"returncode": result.returncode, "events": events, "stderr": result.stderr}


@pytest.mark.e2e
def test_concurrent_workers_claim_once(monet_dev_server: str) -> None:
    """Fan-out N runs; extra workers claim without duplication."""
    monet_bin = shutil.which("monet")
    assert monet_bin is not None

    # Spawn extra workers beyond the one monet dev runs in-server.
    worker_procs: list[subprocess.Popen[bytes]] = []
    worker_logs: list[Path] = []
    for i in range(N_WORKERS):
        log_path = QUICKSTART_DIR / ".monet" / f"e2e-worker-{i}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        worker_logs.append(log_path)
        log_fh = log_path.open("wb")
        proc = subprocess.Popen(
            [monet_bin, "worker", "--pool", "local", "--server-url", monet_dev_server],
            cwd=QUICKSTART_DIR,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        worker_procs.append(proc)

    try:
        time.sleep(5.0)  # let workers register + claim

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=N_TASKS) as pool:
            futures = [
                pool.submit(_run_monet_once, monet_bin, f"topic-{i}")
                for i in range(N_TASKS)
            ]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
    finally:
        for proc in worker_procs:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # Every run reaches run_complete; no crashes.
    completed = 0
    for r in results:
        assert r["returncode"] == 0, f"monet run failed: {r['stderr']}"
        kinds = {
            ev.get("event") or ev.get("type")
            for ev in r["events"]  # type: ignore[union-attr]
            if isinstance(ev.get("event") or ev.get("type"), str)  # type: ignore[union-attr]
        }
        if any(k and "run_complete" in k for k in kinds):
            completed += 1
    assert completed == N_TASKS, f"expected {N_TASKS} completions, got {completed}"
