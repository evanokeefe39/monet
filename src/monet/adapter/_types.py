from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class TaskRequest:
    task_id: str
    task: str
    payload: dict[str, Any]


@dataclass
class TaskResponse:
    output: str
    artifacts: dict[str, str] = field(default_factory=dict)


class ProxyBackend(Protocol):
    async def handle_task(self, request: TaskRequest) -> TaskResponse: ...
    async def close(self) -> None: ...
