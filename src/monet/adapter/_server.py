from __future__ import annotations

import os
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ._config import AdapterConfig, load_config
from ._errors import AdapterError
from ._health import HealthCascade, build_cascade, check_health, wait_healthy
from ._process import start_process, stop_process
from ._proxy_http import HTTPProxy
from ._proxy_openai import OpenAIProxy
from ._proxy_plugin import PluginProxy
from ._proxy_stdio import StdioProxy
from ._types import ProxyBackend, TaskRequest

_ERROR_STATUS: dict[str, int] = {
    "INVALID_REQUEST": 400,
    "UPSTREAM_ERROR": 502,
    "AGENT_ERROR": 500,
    "TIMEOUT": 504,
    "NOT_READY": 503,
}


def _start_stdio_proc(config: AdapterConfig) -> subprocess.Popen[bytes]:
    """Start stdio subprocess with pipes; env merged from [process.env]."""
    env = dict(os.environ)
    env.update(config.process.env)
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": sys.stderr,
        "env": env,
    }
    if config.process.workdir:
        kwargs["cwd"] = config.process.workdir
    return subprocess.Popen(config.stdio.command, **kwargs)


def _build_backend(
    config: AdapterConfig, proc: subprocess.Popen[bytes] | None
) -> ProxyBackend:
    if config.type == "openai":
        return OpenAIProxy(config)
    if config.type == "http":
        return HTTPProxy(config)
    if config.type == "stdio":
        assert proc is not None, "stdio type requires a running process"
        return StdioProxy(config, proc)
    if config.type == "plugin":
        return PluginProxy(config)
    raise ValueError(f"Unknown adapter type: {config.type!r}")


def create_app(config: AdapterConfig) -> FastAPI:
    state: dict[str, Any] = {"proc": None, "backend": None, "cascade": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        proc: subprocess.Popen[bytes] | None = None

        if config.type == "stdio":
            proc = _start_stdio_proc(config)
        elif config.process.command:
            proc = start_process(config.process)

        cascade = build_cascade(config, proc)
        await wait_healthy(cascade, config.ready_timeout)

        backend = _build_backend(config, proc)
        state["proc"] = proc
        state["backend"] = backend
        state["cascade"] = cascade

        try:
            yield
        finally:
            await backend.close()
            if proc:
                stop_process(proc)

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health_endpoint() -> JSONResponse:
        cascade: HealthCascade = state["cascade"] or build_cascade(config)
        ok = await check_health(cascade)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    @app.post("/task")
    async def task_endpoint(request: Request) -> JSONResponse:
        backend: ProxyBackend = state["backend"]
        if backend is None:
            return JSONResponse(
                {"error": "adapter not ready", "error_code": "NOT_READY"},
                status_code=503,
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "invalid JSON", "error_code": "INVALID_REQUEST"},
                status_code=400,
            )

        task_id: str = body.get("task_id", "")
        payload: dict[str, Any] = body.get("payload", {})
        task_text: str = str(payload.get("task", ""))

        try:
            result = await backend.handle_task(
                TaskRequest(task_id=task_id, task=task_text, payload=payload)
            )
            return JSONResponse(
                {
                    "output": result.output,
                    "artifacts": result.artifacts,
                    "success": True,
                }
            )
        except AdapterError as exc:
            status_code = _ERROR_STATUS.get(exc.code, 500)
            return JSONResponse(exc.to_dict(), status_code=status_code)
        except Exception as exc:
            return JSONResponse(
                {"error": str(exc), "error_code": "AGENT_ERROR"}, status_code=500
            )

    return app


def serve(
    config_path: Path,
    host: str = "0.0.0.0",
    port: int | None = None,
) -> None:
    """Load config and run uvicorn."""
    config = load_config(config_path)
    app = create_app(config)
    uvicorn.run(app, host=host, port=port or config.port)
