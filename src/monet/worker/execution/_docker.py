"""Docker execution backend.

Manages agent containers via docker-py. All docker SDK calls are synchronous;
they are offloaded to a thread pool via :func:`asyncio.to_thread` so the event
loop is never blocked.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from ._protocol import ContainerSpec, Endpoint, JobStatus

__all__ = ["DockerBackend"]

_log = logging.getLogger("monet.worker.execution.docker")


@dataclass
class DockerBackend:
    """Runs agent processes as Docker containers.

    Requires the ``docker`` package (``pip install docker``).  The Docker
    daemon must be reachable via the default socket or ``DOCKER_HOST``.

    The returned :class:`~._protocol.Endpoint` has an empty ``address``
    because container-to-container networking uses Docker network names, not
    host-reachable ports.  The ``process_id`` field carries the full container
    ID.
    """

    async def start(self, spec: ContainerSpec, env: dict[str, str]) -> Endpoint:
        """Pull and start a Docker container.

        Args:
            spec: Must have a non-None ``image``.  ``entrypoint`` overrides the
                image default when provided.  ``labels`` are applied to the
                container for discovery.
            env: Environment variables injected into the container.

        Returns:
            :class:`~._protocol.Endpoint` with ``backend_type="docker"`` and
            an empty ``address``.

        Raises:
            RuntimeError: If the ``docker`` package is not installed.
        """
        try:
            import docker  # type: ignore[import-untyped,import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "DockerBackend requires docker: pip install docker"
            ) from exc

        def _run() -> str:
            client = docker.from_env()
            container = client.containers.run(
                spec.image,
                spec.entrypoint,
                detach=True,
                environment=env,
                labels=spec.labels or {},
            )
            return container.id  # type: ignore[no-any-return]

        container_id: str = await asyncio.to_thread(_run)
        _log.debug("docker started: container_id=%s", container_id[:12])
        return Endpoint(
            address="",
            process_id=container_id,
            backend_type="docker",
        )

    async def poll_status(self, endpoint: Endpoint) -> JobStatus:
        """Query the lifecycle state of a Docker container.

        Args:
            endpoint: Returned by :meth:`start` for this container.

        Returns:
            ``RUNNING``, ``SUCCEEDED``, ``FAILED``, or ``UNKNOWN``.
            Returns ``UNKNOWN`` when the container is not found.
        """
        try:
            import docker  # type: ignore[import-untyped,import-not-found]
            import docker.errors  # type: ignore[import-untyped,import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "DockerBackend requires docker: pip install docker"
            ) from exc

        def _poll() -> JobStatus:
            client = docker.from_env()
            try:
                container = client.containers.get(endpoint.process_id)
                container.reload()
            except docker.errors.NotFound:
                return JobStatus.UNKNOWN
            status: str = container.status
            if status == "running":
                return JobStatus.RUNNING
            if status == "exited":
                exit_code: int = container.attrs["State"]["ExitCode"]
                return JobStatus.SUCCEEDED if exit_code == 0 else JobStatus.FAILED
            return JobStatus.UNKNOWN

        return await asyncio.to_thread(_poll)

    async def stop(self, endpoint: Endpoint, grace_period_s: float) -> None:
        """Stop the container with a graceful timeout.

        Calls ``container.stop(timeout=grace_period_s)``. Idempotent — safe to
        call on a container that has already stopped.

        Args:
            endpoint: Returned by :meth:`start` for this container.
            grace_period_s: Timeout passed to Docker stop API.
        """
        try:
            import docker  # type: ignore[import-untyped,import-not-found]
            import docker.errors  # type: ignore[import-untyped,import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "DockerBackend requires docker: pip install docker"
            ) from exc

        def _stop() -> None:
            client = docker.from_env()
            with contextlib.suppress(docker.errors.NotFound):
                client.containers.get(endpoint.process_id).stop(
                    timeout=int(grace_period_s)
                )

        await asyncio.to_thread(_stop)

    async def kill(self, endpoint: Endpoint) -> None:
        """Kill the container immediately.

        Idempotent — safe to call on a container that is already dead.

        Args:
            endpoint: Returned by :meth:`start` for this container.
        """
        try:
            import docker  # type: ignore[import-untyped,import-not-found]
            import docker.errors  # type: ignore[import-untyped,import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "DockerBackend requires docker: pip install docker"
            ) from exc

        def _kill() -> None:
            client = docker.from_env()
            try:
                client.containers.get(endpoint.process_id).kill()
            except docker.errors.NotFound:
                pass
            except docker.errors.APIError:
                # Container already dead — suppress to maintain idempotency.
                pass

        await asyncio.to_thread(_kill)
