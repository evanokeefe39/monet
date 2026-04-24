"""GCP Cloud Run dispatch backend."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.queue._dispatch import ClaimedTask

_log = logging.getLogger("monet.queue.dispatch.cloudrun")


@dataclass
class CloudRunDispatchBackend:
    project: str
    region: str
    job: str

    async def submit(self, task: ClaimedTask, server_url: str, api_key: str) -> None:
        try:
            from google.cloud.run_v2 import (  # type: ignore[import-untyped,import-not-found]
                JobsAsyncClient,
                RunJobRequest,
            )
        except ImportError as exc:
            raise RuntimeError(
                "CloudRunDispatchBackend requires google-cloud-run: "
                "pip install google-cloud-run"
            ) from exc
        client = JobsAsyncClient()
        name = f"projects/{self.project}/locations/{self.region}/jobs/{self.job}"
        overrides = {
            "containerOverrides": [
                {
                    "env": [
                        {"name": "MONET_TASK_ID", "value": task["task_id"]},
                        {"name": "MONET_AGENT_ID", "value": task["agent_id"]},
                        {"name": "MONET_COMMAND", "value": task["command"]},
                        {"name": "MONET_RUN_ID", "value": task["run_id"]},
                        {"name": "MONET_THREAD_ID", "value": task["thread_id"]},
                        {"name": "MONET_POOL", "value": task["pool"]},
                        {"name": "MONET_SERVER_URL", "value": server_url},
                        {"name": "MONET_API_KEY", "value": api_key},
                    ]
                }
            ]
        }
        await client.run_job(RunJobRequest(name=name, overrides=overrides))
        _log.debug(
            "cloudrun dispatch: submitted task=%s job=%s", task["task_id"], self.job
        )
