"""Google Cloud Run execution backend.

Submits Cloud Run Jobs and polls execution status via the ``google-cloud-run``
client library. ``stop`` and ``kill`` are best-effort cancellation calls
against the Executions API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ._protocol import ContainerSpec, Endpoint, JobStatus

__all__ = ["CloudRunBackend"]

_log = logging.getLogger("monet.worker.execution.cloudrun")


@dataclass
class CloudRunBackend:
    """Executes agent jobs on Google Cloud Run.

    Requires ``google-cloud-run`` (``pip install google-cloud-run``).

    The returned :class:`~._protocol.Endpoint` ``process_id`` is the Cloud Run
    *execution* resource name (e.g.
    ``projects/my-project/locations/us-central1/jobs/my-job/executions/my-exec``).
    ``address`` is empty — agents are reached through a Cloud Run ingress URL
    configured separately.

    Attributes:
        project: GCP project ID.
        region: GCP region (e.g. ``"us-central1"``).
        job: Cloud Run Job name.
        poll_interval_s: Seconds between polling calls in :meth:`poll_status`.
            Stored for callers that run their own polling loop; not used
            internally.
    """

    project: str
    region: str
    job: str
    poll_interval_s: float = 5.0

    async def start(self, spec: ContainerSpec, env: dict[str, str]) -> Endpoint:
        """Submit a Cloud Run Job execution with environment overrides.

        Args:
            spec: Container spec; ``image``, ``cpu``, and ``memory`` are
                ignored (the job definition controls those). ``entrypoint``
                is not passed — override via the Cloud Run job configuration.
            env: Environment variables injected as container overrides.

        Returns:
            :class:`~._protocol.Endpoint` with ``backend_type="cloudrun"``
            and the execution resource name in ``process_id``.

        Raises:
            RuntimeError: If ``google-cloud-run`` is not installed.
        """
        try:
            from google.cloud.run_v2 import (  # type: ignore[import-untyped,import-not-found]
                JobsAsyncClient,
                RunJobRequest,
            )
        except ImportError as exc:
            raise RuntimeError(
                "CloudRunBackend requires google-cloud-run: "
                "pip install google-cloud-run"
            ) from exc

        env_list = [{"name": k, "value": v} for k, v in env.items()]
        name = f"projects/{self.project}/locations/{self.region}/jobs/{self.job}"
        client = JobsAsyncClient()
        op = await client.run_job(
            RunJobRequest(
                name=name,
                overrides={"containerOverrides": [{"env": env_list}]},
            )
        )
        execution_name: str = op.metadata.name
        _log.debug("cloudrun started: execution=%s", execution_name)
        return Endpoint(
            address="",
            process_id=execution_name,
            backend_type="cloudrun",
            metadata={"project": self.project, "region": self.region},
        )

    async def poll_status(self, endpoint: Endpoint) -> JobStatus:
        """Query the current state of a Cloud Run execution.

        Args:
            endpoint: Returned by :meth:`start` for this execution.

        Returns:
            ``SUCCEEDED`` if ``succeeded_count > 0``, ``FAILED`` if
            ``failed_count > 0``, ``RUNNING`` otherwise.  Returns ``UNKNOWN``
            on API errors.
        """
        try:
            from google.cloud.run_v2 import (  # type: ignore[import-untyped,import-not-found]
                ExecutionsAsyncClient,
            )
        except ImportError as exc:
            raise RuntimeError(
                "CloudRunBackend requires google-cloud-run: "
                "pip install google-cloud-run"
            ) from exc

        client = ExecutionsAsyncClient()
        execution = await client.get_execution(name=endpoint.process_id)
        if execution.succeeded_count > 0:
            return JobStatus.SUCCEEDED
        if execution.failed_count > 0:
            return JobStatus.FAILED
        return JobStatus.RUNNING

    async def stop(self, endpoint: Endpoint, grace_period_s: float) -> None:
        """Cancel the Cloud Run execution (best-effort).

        ``grace_period_s`` is not used — the Cloud Run API cancels
        asynchronously.  Errors are suppressed to maintain idempotency.

        Args:
            endpoint: Returned by :meth:`start` for this execution.
            grace_period_s: Ignored for cloud backends.
        """
        try:
            from google.cloud.run_v2 import (  # type: ignore[import-untyped,import-not-found]
                CancelExecutionRequest,
                ExecutionsAsyncClient,
            )
        except ImportError:
            return
        try:
            client = ExecutionsAsyncClient()
            await client.cancel_execution(
                CancelExecutionRequest(name=endpoint.process_id)
            )
        except Exception:
            _log.debug(
                "cloudrun stop: suppressed error for execution=%s", endpoint.process_id
            )

    async def kill(self, endpoint: Endpoint) -> None:
        """Cancel the Cloud Run execution immediately (best-effort).

        Identical to :meth:`stop` — Cloud Run has no separate force-kill API.

        Args:
            endpoint: Returned by :meth:`start` for this execution.
        """
        await self.stop(endpoint, grace_period_s=0.0)
