"""Execution backend protocols and implementations.

Execution backends manage the lifecycle of agent processes — starting,
polling, stopping, and killing containers, subprocesses, or cloud jobs.
Each backend implements :class:`ExecutionBackend` and returns an
:class:`Endpoint` that transport adapters use to connect.
"""

from __future__ import annotations

from ._cloudrun import CloudRunBackend
from ._docker import DockerBackend
from ._ecs import ECSBackend
from ._protocol import ContainerSpec, Endpoint, ExecutionBackend, JobStatus
from ._subprocess import SubprocessBackend

__all__ = [
    "CloudRunBackend",
    "ContainerSpec",
    "DockerBackend",
    "ECSBackend",
    "Endpoint",
    "ExecutionBackend",
    "JobStatus",
    "SubprocessBackend",
]
