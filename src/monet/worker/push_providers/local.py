"""Local dispatch backend: spawns a subprocess per task. Dev and testing."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.events._tasks import ClaimedTask

_log = logging.getLogger("monet.worker.dispatch.local")


class LocalDispatchBackend:
    """Dispatch backend that spawns an in-process subprocess per task."""

    async def submit(self, task: ClaimedTask, server_url: str, api_key: str) -> None:
        # monet.worker.push_providers._dispatch_subprocess does not yet exist.
        # Tracked in ISSUES.md: LocalDispatchBackend subprocess module missing.
        cmd = [
            sys.executable,
            "-m",
            "monet.worker.push_providers._dispatch_subprocess",
            "--task-id",
            task["task_id"],
            "--agent-id",
            task["agent_id"],
            "--command",
            task["command"],
            "--run-id",
            task["run_id"],
            "--thread-id",
            task["thread_id"],
            "--pool",
            task["pool"],
            "--server-url",
            server_url,
        ]
        env_extra = {"MONET_API_KEY": api_key, "MONET_SERVER_URL": server_url}
        env = {**os.environ, **env_extra}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        _log.debug("local dispatch: spawned pid=%d task=%s", proc.pid, task["task_id"])
