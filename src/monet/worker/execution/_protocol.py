"""Execution backend protocol and shared data types.

Defines :class:`ExecutionBackend`, the runtime-checkable protocol every
backend must implement, along with the supporting types :class:`Endpoint`,
:class:`ContainerSpec`, and :class:`JobStatus`.

Design constraints
------------------
- No runtime imports beyond stdlib + typing. Backend implementations live
  in sibling modules and import their heavy dependencies (docker-py, boto3,
  google-cloud-run) lazily inside their methods.
- All backends implement the same four-method surface regardless of whether
  they manage local processes (subprocess/docker) or cloud jobs (cloudrun/ecs).
  Cloud backends treat ``stop()`` / ``kill()`` as best-effort cancellation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

__all__ = ["ContainerSpec", "Endpoint", "ExecutionBackend", "JobStatus"]


class JobStatus(Enum):
    """Lifecycle state of an agent job as reported by the backend.

    Attributes:
        RUNNING: The job is still executing.
        SUCCEEDED: The job completed successfully.
        FAILED: The job terminated with an error or non-zero exit code.
        UNKNOWN: The backend cannot determine the current state (e.g. the
            cloud API returned an unrecognised status value).
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Endpoint:
    """Describes where and how an agent process can be reached.

    Returned by :meth:`ExecutionBackend.start` and passed to transport
    adapters so they can open a :class:`~monet.worker.transport.Session`.

    Attributes:
        address: Network address or URI scheme address for the agent.
            Examples: ``"http://127.0.0.1:8080"`` (HTTP/SSE),
            ``"cli://pid/1234"`` (CLI transport).
        process_id: Opaque identifier for the running process or job.
            Examples: container ID, Unix PID string, ECS task ARN,
            Cloud Run execution ID.
        backend_type: Which backend started this process — one of
            ``"subprocess"``, ``"docker"``, ``"kubernetes"``,
            ``"cloudrun"``, ``"ecs"``.
        metadata: Backend-specific key/value pairs (e.g. port number, region,
            cluster name). Not interpreted by the workload layer.
    """

    address: str
    process_id: str
    backend_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContainerSpec:
    """Minimal description of what to run.

    Passed to :meth:`ExecutionBackend.start`. Only the fields needed by the
    target backend need to be populated; unused fields are ``None``.

    Attributes:
        image: Container image reference (docker/cloudrun/ecs backends).
        entrypoint: Command to run inside the container, overriding the image
            default entrypoint (e.g. ``["python", "-m", "myagent"]``).
        cpu: CPU allocation string in backend-native format (e.g. ``"1"`` for
            Cloud Run, ``"1024"`` for ECS task units).
        memory: Memory limit in MiB (integer).
        labels: Key/value labels to attach to the container for discovery and
            orphan reconciliation.
    """

    image: str | None = None
    entrypoint: list[str] | None = None
    cpu: str | None = None
    memory: int | None = None
    labels: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ExecutionBackend(Protocol):
    """Manages the lifecycle of a single agent process or cloud job.

    All methods are coroutines so the event loop is never blocked.

    Implementations:
        - ``SubprocessBackend`` — spawns a local subprocess.
        - ``DockerBackend`` — runs a Docker container via docker-py.
        - ``CloudRunBackend`` — submits a Cloud Run job; polls via cloud API.
        - ``ECSBackend`` — submits an ECS task; polls via AWS API.
        - ``KubernetesBackend`` — dispatches to an existing K8s deployment.

    Invariant:
        :meth:`start` must complete before any other method is called.
        :meth:`stop` and :meth:`kill` are idempotent.
    """

    async def start(
        self,
        spec: ContainerSpec,
        env: dict[str, str],
    ) -> Endpoint:
        """Start the agent process or submit the cloud job.

        Args:
            spec: What to run — image, entrypoint, resource limits, labels.
            env: Environment variables to inject into the process.

        Returns:
            :class:`Endpoint` describing where to connect and how to
            reference this process in subsequent calls.

        Raises:
            RuntimeError: If the backend cannot start the process (e.g. image
                not found, quota exceeded).
        """
        ...

    async def poll_status(self, endpoint: Endpoint) -> JobStatus:
        """Query the current lifecycle state of the process.

        For local backends (subprocess, docker), checks the process exit
        code or container status.  For cloud backends (cloudrun, ecs), calls
        the cloud API.

        Args:
            endpoint: Returned by :meth:`start` for this process.

        Returns:
            Current :class:`JobStatus`. Returns ``UNKNOWN`` rather than
            raising if the backend cannot determine the state.
        """
        ...

    async def stop(self, endpoint: Endpoint, grace_period_s: float) -> None:
        """Gracefully request the process to stop.

        Sends SIGTERM (local) or calls the cloud API cancellation endpoint.
        Waits up to *grace_period_s* before returning.  If the process has
        not exited by then the caller is responsible for calling :meth:`kill`.

        Idempotent — safe to call on an already-stopped process.

        Args:
            endpoint: Returned by :meth:`start` for this process.
            grace_period_s: Maximum seconds to wait for graceful exit.
        """
        ...

    async def kill(self, endpoint: Endpoint) -> None:
        """Forcibly terminate the process without waiting.

        Sends SIGKILL (local) or calls a force-cancel API (cloud). Returns
        immediately without waiting for process exit.

        Idempotent — safe to call multiple times or on an already-dead process.

        Args:
            endpoint: Returned by :meth:`start` for this process.
        """
        ...
