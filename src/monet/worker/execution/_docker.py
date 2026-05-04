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

        When ``spec.expose_port`` is set, the container port is published to a
        random host port and the returned :class:`Endpoint` carries a reachable
        ``http://localhost:{host_port}`` address.  Otherwise ``address`` is empty.

        Args:
            spec: Must have a non-None ``image``.  ``entrypoint`` overrides the
                image default when provided.  ``labels`` are applied to the
                container for discovery.
            env: Environment variables injected into the container.

        Returns:
            :class:`~._protocol.Endpoint` with ``backend_type="docker"``.

        Raises:
            RuntimeError: If the ``docker`` package is not installed.
        """
        try:
            import docker  # type: ignore[import-untyped,import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "DockerBackend requires docker: pip install docker"
            ) from exc

        expose = spec.expose_port

        def _run() -> tuple[str, str]:
            client = docker.from_env()
            ports: dict[str, object] | None = None
            if expose is not None:
                ports = {f"{expose}/tcp": ("127.0.0.1", 0)}
            container = client.containers.run(
                spec.image,
                spec.entrypoint,
                detach=True,
                environment=env,
                labels=spec.labels or {},
                ports=ports,
            )
            address = ""
            if expose is not None:
                container.reload()
                port_map = container.ports.get(f"{expose}/tcp")
                if port_map:
                    host_port = port_map[0]["HostPort"]
                    address = f"http://localhost:{host_port}"
            return container.id, address  # type: ignore[return-value]

        container_id, address = await asyncio.to_thread(_run)
        _log.debug(
            "docker started: container_id=%s address=%s",
            container_id[:12],
            address or "(none)",
        )
        return Endpoint(
            address=address,
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
