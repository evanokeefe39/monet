"""Dispatch backend protocol for submitting tasks to external compute."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from monet.events._tasks import ClaimedTask

__all__ = ["ClaimedTask", "DispatchBackend"]


@runtime_checkable
class DispatchBackend(Protocol):
    async def submit(
        self,
        task: ClaimedTask,
        server_url: str,
        api_key: str,
    ) -> None:
        """Submit task to compute backend. Returns after submission, not completion.

        The submitted container calls WorkerClient.complete/fail and renews
        the lease directly. Dispatch worker has no further responsibility.
        """
        ...
