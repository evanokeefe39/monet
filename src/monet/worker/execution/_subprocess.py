"""Subprocess execution backend.

Spawns agent processes as local OS subprocesses using :mod:`asyncio.subprocess`.
Intended for local development and single-machine deployments.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from asyncio.subprocess import DEVNULL
from dataclasses import dataclass, field

from ._protocol import ContainerSpec, Endpoint, JobStatus

__all__ = ["SubprocessBackend"]

_log = logging.getLogger("monet.worker.execution.subprocess")


def _free_port() -> int:
    """Bind to port 0 and return the OS-assigned port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class SubprocessBackend:
    """Runs agent processes as local subprocesses.

    Each :meth:`start` call spawns a new subprocess and records it so that
    :meth:`poll_status`, :meth:`stop`, and :meth:`kill` can reference it by
    PID string stored in the returned :class:`~._protocol.Endpoint`.
    """

    _procs: dict[str, asyncio.subprocess.Process] = field(
        default_factory=dict, init=False
    )

    async def start(self, spec: ContainerSpec, env: dict[str, str]) -> Endpoint:
        """Start a subprocess from *spec.entrypoint*.

        Args:
            spec: Must have a non-empty ``entrypoint`` list.
            env: Environment variables injected into the subprocess.
                ``MONET_AGENT_PORT`` is added automatically.

        Returns:
            :class:`~._protocol.Endpoint` with ``backend_type="subprocess"``
            and ``address="http://127.0.0.1:<port>"``.

        Raises:
            RuntimeError: If ``spec.entrypoint`` is None or empty.
        """
        if not spec.entrypoint:
            raise RuntimeError(
                "SubprocessBackend.start: spec.entrypoint must be a non-empty list"
            )

        port = _free_port()
        merged_env = {**env, "MONET_AGENT_PORT": str(port)}

        proc = await asyncio.create_subprocess_exec(
            *spec.entrypoint,
            env=merged_env,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
        pid_str = str(proc.pid)
        self._procs[pid_str] = proc
        _log.debug("subprocess started: pid=%s port=%s", pid_str, port)
        return Endpoint(
            address=f"http://127.0.0.1:{port}",
            process_id=pid_str,
            backend_type="subprocess",
            metadata={"port": port},
        )

    async def poll_status(self, endpoint: Endpoint) -> JobStatus:
        """Query the lifecycle state of the subprocess.

        Args:
            endpoint: Returned by :meth:`start` for this process.

        Returns:
            ``RUNNING`` if the process is still alive, ``SUCCEEDED`` if it
            exited with code 0, ``FAILED`` for a non-zero exit code, or
            ``UNKNOWN`` if the PID is not tracked by this instance.
        """
        proc = self._procs.get(endpoint.process_id)
        if proc is None:
            return JobStatus.UNKNOWN
        rc = proc.returncode
        if rc is None:
            return JobStatus.RUNNING
        return JobStatus.SUCCEEDED if rc == 0 else JobStatus.FAILED

    async def stop(self, endpoint: Endpoint, grace_period_s: float) -> None:
        """Send SIGTERM and wait up to *grace_period_s* for exit.

        Idempotent — safe to call on a process that has already exited.

        Args:
            endpoint: Returned by :meth:`start` for this process.
            grace_period_s: Maximum seconds to wait after SIGTERM.
        """
        proc = self._procs.get(endpoint.process_id)
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=grace_period_s)
        except TimeoutError:
            _log.debug(
                "subprocess stop: timeout waiting for pid=%s", endpoint.process_id
            )

    async def kill(self, endpoint: Endpoint) -> None:
        """Send SIGKILL without waiting.

        Idempotent — safe to call multiple times or on a dead process.

        Args:
            endpoint: Returned by :meth:`start` for this process.
        """
        proc = self._procs.get(endpoint.process_id)
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
