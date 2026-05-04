"""Worker workload execution — composition layer.

Sequences backend lifecycle, transport sessions, and lease renewal into
three execution paths:

- :func:`execute_managed_workload` — per-task backend lifecycle
  (subprocess/docker/cloud).
- :func:`execute_persistent_workload` — acquire from warm pool, submit, release.
- :func:`execute_cloud_push_workload` — cloud dispatch + poll loop (Cloud Run / ECS).

Supporting types:
- :class:`ManagedInstance` — a single running agent in a persistent pool.
- :class:`TaskRouter` — idle/busy tracking with blocking acquire.
- :class:`ContainerSupervisor` — warm pool startup, liveness, restarts, drain.
- :class:`TaskFailure` — signals that a task should be posted to queue.fail().
"""

from monet.worker.workload._collect import TaskFailure
from monet.worker.workload._managed import execute_managed_workload
from monet.worker.workload._persistent import (
    execute_cloud_push_workload,
    execute_persistent_workload,
)
from monet.worker.workload._router import ManagedInstance, TaskRouter
from monet.worker.workload._supervisor import ContainerSupervisor

__all__ = [
    "ContainerSupervisor",
    "ManagedInstance",
    "TaskFailure",
    "TaskRouter",
    "execute_cloud_push_workload",
    "execute_managed_workload",
    "execute_persistent_workload",
]
