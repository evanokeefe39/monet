"""Execution backend protocols and implementations.

Execution backends manage the lifecycle of agent processes — starting,
polling, stopping, and killing containers, subprocesses, or cloud jobs.
Each backend implements :class:`ExecutionBackend` and returns an
:class:`Endpoint` that transport adapters use to connect.
"""

from __future__ import annotations

from ._protocol import ContainerSpec, Endpoint, ExecutionBackend, JobStatus

__all__ = [
    "ContainerSpec",
    "Endpoint",
    "ExecutionBackend",
    "JobStatus",
]
