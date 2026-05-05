from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Any

from ._errors import AdapterError
from ._types import TaskRequest, TaskResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._config import AdapterConfig


class PluginProxy:
    def __init__(self, config: AdapterConfig) -> None:
        assert config.plugin is not None
        module_path, fn_name = config.plugin.rsplit(":", 1)
        module = importlib.import_module(module_path)
        self._fn: Callable[..., Any] = getattr(module, fn_name)

    async def handle_task(self, request: TaskRequest) -> TaskResponse:
        try:
            result: dict[str, Any] = await asyncio.to_thread(
                self._fn, request.task_id, request.payload
            )
            return TaskResponse(
                output=result["output"],
                artifacts=result.get("artifacts", {}),
            )
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(str(exc), "AGENT_ERROR") from exc

    async def close(self) -> None:
        pass
