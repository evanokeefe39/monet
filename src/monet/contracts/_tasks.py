from __future__ import annotations

from typing_extensions import TypedDict

__all__ = ["ClaimedTask"]


class ClaimedTask(TypedDict):
    task_id: str
    run_id: str
    thread_id: str
    agent_id: str
    command: str
    pool: str
