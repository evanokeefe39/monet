"""AWS ECS execution backend.

Submits Fargate tasks and polls status via the ``aioboto3`` async AWS client.
``stop`` and ``kill`` both call ``ecs:StopTask`` — ECS has no separate
force-kill primitive.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ._protocol import ContainerSpec, Endpoint, JobStatus

__all__ = ["ECSBackend"]

_log = logging.getLogger("monet.worker.execution.ecs")


@dataclass
class ECSBackend:
    """Executes agent jobs as AWS ECS Fargate tasks.

    Requires ``aioboto3`` (``pip install aioboto3``).

    The returned :class:`~._protocol.Endpoint` ``process_id`` is the ECS task
    ARN. ``address`` is empty — agents are reached via an ALB or service
    discovery configured separately.

    Attributes:
        cluster: ECS cluster name or ARN.
        task_definition: Task definition name or ARN.
        subnet_ids: VPC subnet IDs for the Fargate ``awsvpcConfiguration``.
        security_groups: Security group IDs for the Fargate network config.
    """

    cluster: str
    task_definition: str
    subnet_ids: list[str] = field(default_factory=list)
    security_groups: list[str] = field(default_factory=list)

    async def start(self, spec: ContainerSpec, env: dict[str, str]) -> Endpoint:
        """Submit an ECS Fargate task with environment overrides.

        Args:
            spec: Container spec; ``image``, ``cpu``, ``memory``, and
                ``entrypoint`` are not passed to ECS (controlled by the task
                definition).
            env: Environment variables injected as container overrides on the
                ``"worker"`` container.

        Returns:
            :class:`~._protocol.Endpoint` with ``backend_type="ecs"`` and the
            task ARN in ``process_id``.

        Raises:
            RuntimeError: If ``aioboto3`` is not installed.
        """
        try:
            import aioboto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "ECSBackend requires aioboto3: pip install aioboto3"
            ) from exc

        env_overrides = [{"name": k, "value": v} for k, v in env.items()]
        session = aioboto3.Session()
        async with session.client("ecs") as ecs:
            response = await ecs.run_task(
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
                            "environment": env_overrides,
                        }
                    ]
                },
            )
        task_arn: str = response["tasks"][0]["taskArn"]
        _log.debug("ecs started: task_arn=%s", task_arn)
        return Endpoint(
            address="",
            process_id=task_arn,
            backend_type="ecs",
            metadata={"cluster": self.cluster},
        )

    async def poll_status(self, endpoint: Endpoint) -> JobStatus:
        """Query the current state of an ECS task.

        Args:
            endpoint: Returned by :meth:`start` for this task.

        Returns:
            ``RUNNING`` while the task is active, ``SUCCEEDED`` if it stopped
            with exit code 0, ``FAILED`` for a non-zero exit code, ``UNKNOWN``
            if the task list is empty or the status is unrecognised.
        """
        try:
            import aioboto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "ECSBackend requires aioboto3: pip install aioboto3"
            ) from exc

        cluster = endpoint.metadata.get("cluster", self.cluster)
        session = aioboto3.Session()
        async with session.client("ecs") as ecs:
            response = await ecs.describe_tasks(
                cluster=cluster,
                tasks=[endpoint.process_id],
            )

        tasks = response.get("tasks", [])
        if not tasks:
            return JobStatus.UNKNOWN

        task = tasks[0]
        last_status: str = task.get("lastStatus", "")

        if last_status == "RUNNING":
            return JobStatus.RUNNING

        if last_status == "STOPPED":
            exit_code: int | None = task.get("containers", [{}])[0].get("exitCode")
            if exit_code is None:
                return JobStatus.UNKNOWN
            return JobStatus.SUCCEEDED if exit_code == 0 else JobStatus.FAILED

        # PROVISIONING, PENDING, DEPROVISIONING, etc. are transitional.
        return JobStatus.RUNNING

    async def stop(self, endpoint: Endpoint, grace_period_s: float) -> None:
        """Stop the ECS task (best-effort).

        ``grace_period_s`` is not used — ECS ``StopTask`` is asynchronous.
        Errors are suppressed to maintain idempotency.

        Args:
            endpoint: Returned by :meth:`start` for this task.
            grace_period_s: Ignored for cloud backends.
        """
        try:
            import aioboto3  # type: ignore[import-not-found]
        except ImportError:
            return
        cluster = endpoint.metadata.get("cluster", self.cluster)
        try:
            session = aioboto3.Session()
            async with session.client("ecs") as ecs:
                await ecs.stop_task(
                    cluster=cluster,
                    task=endpoint.process_id,
                    reason="stop requested",
                )
        except Exception:
            _log.debug("ecs stop: suppressed error for task=%s", endpoint.process_id)

    async def kill(self, endpoint: Endpoint) -> None:
        """Stop the ECS task immediately (best-effort).

        Identical to :meth:`stop` — ECS has no separate force-kill primitive.

        Args:
            endpoint: Returned by :meth:`start` for this task.
        """
        await self.stop(endpoint, grace_period_s=0.0)
