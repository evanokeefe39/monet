"""AWS ECS dispatch backend."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.queue._dispatch import ClaimedTask

_log = logging.getLogger("monet.queue.dispatch.ecs")


@dataclass
class ECSDispatchBackend:
    cluster: str
    task_definition: str
    subnet_ids: list[str] = field(default_factory=list)
    security_groups: list[str] = field(default_factory=list)

    async def submit(self, task: ClaimedTask, server_url: str, api_key: str) -> None:
        try:
            import aioboto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "ECSDispatchBackend requires aioboto3: pip install aioboto3"
            ) from exc
        session = aioboto3.Session()
        async with session.client("ecs") as ecs:
            await ecs.run_task(
                cluster=self.cluster,
                taskDefinition=self.task_definition,
                launchType="FARGATE",
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": self.subnet_ids,
                        "securityGroups": self.security_groups,
                    }
                },
                overrides={
                    "containerOverrides": [
                        {
                            "name": "worker",
                            "environment": [
                                {
                                    "name": "MONET_TASK_ID",
                                    "value": task["task_id"],
                                },
                                {
                                    "name": "MONET_AGENT_ID",
                                    "value": task["agent_id"],
                                },
                                {
                                    "name": "MONET_COMMAND",
                                    "value": task["command"],
                                },
                                {"name": "MONET_RUN_ID", "value": task["run_id"]},
                                {
                                    "name": "MONET_THREAD_ID",
                                    "value": task["thread_id"],
                                },
                                {"name": "MONET_POOL", "value": task["pool"]},
                                {"name": "MONET_SERVER_URL", "value": server_url},
                                {"name": "MONET_API_KEY", "value": api_key},
                            ],
                        }
                    ]
                },
            )
        _log.debug(
            "ecs dispatch: submitted task=%s cluster=%s",
            task["task_id"],
            self.cluster,
        )
