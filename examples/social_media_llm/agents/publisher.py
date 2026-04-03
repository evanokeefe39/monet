"""Publisher implementation — CLI subprocess agent, zero monet imports.

Wraps a subprocess call to publisher_cli.py for content formatting.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path


async def run_publisher(task: str) -> list[dict[str, object]]:
    """Run the publisher CLI and collect events from stdout.

    Returns a list of JSON events emitted by the subprocess.
    """
    cli_script = str(Path(__file__).parent / "publisher_cli.py")

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        cli_script,
        "--task",
        task,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    events: list[dict[str, object]] = []
    assert proc.stdout is not None
    async for line in proc.stdout:
        line_str = line.decode().strip()
        if line_str:
            with contextlib.suppress(json.JSONDecodeError):
                events.append(json.loads(line_str))

    await proc.wait()

    if proc.returncode != 0:
        assert proc.stderr is not None
        stderr = await proc.stderr.read()
        msg = f"Publisher CLI failed (rc={proc.returncode}): {stderr.decode()}"
        raise RuntimeError(msg)

    return events
