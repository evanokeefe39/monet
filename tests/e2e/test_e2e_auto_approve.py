"""E2E-01 — auto-approve happy path.

Runs ``monet run "topic" --auto-approve`` against a real ``monet dev``
server. Validates exit code, JSON event stream shape, and that a
``RunComplete`` event reaches the client.

Depends on the ``monet_dev_server`` session fixture and on LLM
credentials loaded from the example's ``.env``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
QUICKSTART_DIR = REPO_ROOT / "examples" / "quickstart"
RUN_TIMEOUT_SECONDS = 300.0


@pytest.mark.e2e
def test_auto_approve_happy_path(monet_dev_server: str) -> None:
    """``monet run --auto-approve`` drives the pipeline to completion."""
    monet_bin = shutil.which("monet")
    assert monet_bin is not None, "'monet' script not on PATH"

    result = subprocess.run(
        [
            monet_bin,
            "run",
            "AI trends in healthcare",
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

    assert result.returncode == 0, (
        f"monet run exited {result.returncode}. stderr:\n{result.stderr}"
    )

    events: list[dict[str, object]] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        events.append(json.loads(line))

    assert events, "no JSON events emitted"
    kinds: set[str] = set()
    for ev in events:
        k = ev.get("event") or ev.get("type")
        if isinstance(k, str):
            kinds.add(k)
    assert any("RunComplete" in k for k in kinds), f"no RunComplete in events: {kinds}"
