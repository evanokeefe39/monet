from __future__ import annotations

import asyncio
import importlib
import json
import threading
from typing import TYPE_CHECKING, Any

from ._errors import AdapterError
from ._types import TaskRequest, TaskResponse

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable

    from ._config import AdapterConfig

    RpcFn = Callable[[str, dict[str, Any]], dict[str, Any]]


class StdioProxy:
    def __init__(self, config: AdapterConfig, proc: subprocess.Popen[bytes]) -> None:
        self._config = config
        self._proc = proc
        self._lock = threading.Lock()
        self._next_id = 0
        self._plugin_fn = self._load_plugin()
        if config.stdio.init_rpc:
            self._rpc(config.stdio.init_rpc, {})

    def _load_plugin(self) -> Callable[..., Any]:
        module_path, fn_name = self._config.stdio.plugin.rsplit(":", 1)
        module = importlib.import_module(module_path)
        return getattr(module, fn_name)  # type: ignore[no-any-return]

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send JSON-RPC 2.0 request; accumulate notifications; return result."""
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None

        self._next_id += 1
        req_id = self._next_id
        msg = json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        )
        self._proc.stdin.write((msg + "\n").encode())
        self._proc.stdin.flush()

        streamed: list[str] = []
        while True:
            raw = self._proc.stdout.readline()
            if not raw:
                raise AdapterError(
                    "Subprocess closed stdout unexpectedly", "UPSTREAM_ERROR"
                )
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Notification — no id field
            if "id" not in envelope:
                p = envelope.get("params", {})
                for key in ("chunk", "content", "text", "delta"):
                    if key in p and isinstance(p[key], str):
                        streamed.append(p[key])
                        break
                continue

            if envelope.get("id") != req_id:
                continue

            if "error" in envelope:
                raise AdapterError(str(envelope["error"]), "UPSTREAM_ERROR")

            result: dict[str, Any] = envelope.get("result") or {}
            if streamed:
                result["_streamed"] = "".join(streamed)
            return result

    def _locked_rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._rpc(method, params)

    async def handle_task(self, request: TaskRequest) -> TaskResponse:
        try:
            output = await asyncio.to_thread(
                self._plugin_fn, self._locked_rpc, request.task
            )
            return TaskResponse(output=str(output))
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(str(exc), "AGENT_ERROR") from exc

    async def close(self) -> None:
        pass
