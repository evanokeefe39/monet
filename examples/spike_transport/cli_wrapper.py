"""CLI agent wrapper — wraps a subprocess that emits ndjson to stdout.

Demonstrates the CLI wrapping pattern from the spec: spawn a subprocess,
read ndjson from stdout, forward progress events, return the final result.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from .decorator import agent

_CLI_SCRIPT = str(Path(__file__).parent / "cli_agent.py")


@agent(agent_id="cli-analyst", command="fast")
async def cli_analyst(task: str, run_id: str, effort: str) -> str:
    """Wrap the CLI agent as a subprocess."""
    effort_val = effort if effort else "high"

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        _CLI_SCRIPT,
        "--task",
        task,
        "--run-id",
        run_id,
        "--effort",
        effort_val,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    progress_events: list[dict[str, object]] = []
    result_output = ""

    assert proc.stdout is not None
    async for line in proc.stdout:
        text = line.decode().strip()
        if not text:
            continue
        event = json.loads(text)
        if event["type"] == "progress":
            progress_events.append(event)
        elif event["type"] == "result":
            result_output = str(event["output"])

    await proc.wait()

    if proc.returncode != 0:
        stderr_bytes = await proc.stderr.read() if proc.stderr else b""
        msg = f"CLI agent exited with code {proc.returncode}: {stderr_bytes.decode()}"
        raise RuntimeError(msg)

    return result_output
