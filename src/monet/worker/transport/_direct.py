"""Protocol-native callers for config-declared agents.

Each factory returns a coroutine  ``caller(task, ctx) -> str | None`` where
*ctx* is the JSONPath context dict::

    {task, task_id, context, command, agent_id}

No adapter server required.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import httpx

from monet.core._jsonpath import extract

if TYPE_CHECKING:
    from monet.core.agent_loader import AgentHTTPRequest, AgentHTTPResponse

CallerFn = Callable[[str, dict[str, Any]], Coroutine[Any, Any, str | None]]


# ── helpers ─────────────────────────────────────────────────────────────────


def _normalize_openai_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        return urlunparse(parsed._replace(path="/v1/chat/completions"))
    if path == "/v1":
        return urlunparse(parsed._replace(path="/v1/chat/completions"))
    return url


def _resolve_body(template: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, val in template.items():
        if isinstance(val, str) and val.startswith("$."):
            result[key] = extract(ctx, val)
        elif isinstance(val, dict):
            result[key] = _resolve_body(val, ctx)
        else:
            result[key] = val
    return result


# ── protocol callers ─────────────────────────────────────────────────────────


def openai_caller(url: str, model: str | None, timeout: float) -> CallerFn:
    """POST to an OpenAI-compatible /v1/chat/completions endpoint."""
    normalized = _normalize_openai_url(url)

    async def call(task: str, ctx: dict[str, Any]) -> str | None:
        body: dict[str, Any] = {
            "messages": [{"role": "user", "content": task}],
        }
        if model:
            body["model"] = model

        headers: dict[str, str] = {"Content-Type": "application/json"}
        key = os.environ.get("OPENAI_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(normalized, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return str(data["choices"][0]["message"]["content"])

    return call


def http_caller(
    url: str,
    request_cfg: AgentHTTPRequest,
    response_cfg: AgentHTTPResponse,
    timeout: float,
) -> CallerFn:
    """POST to an arbitrary HTTP endpoint using JSONPath body templates."""

    async def call(task: str, ctx: dict[str, Any]) -> str | None:
        body = _resolve_body(request_cfg.body, ctx)
        params = request_cfg.params

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                request_cfg.method,
                url,
                json=body,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
        return str(extract(data, response_cfg.output))

    return call


def zeroclaw_caller(config_dir: str | None, timeout: float) -> CallerFn:
    """Spawn zeroclaw ACP as a subprocess per invocation (local dev mode)."""

    def _resolve_config_dir() -> str:
        if config_dir:
            return config_dir
        return os.environ.get("ZEROCLAW_CONFIG_DIR", os.path.expanduser("~/.zeroclaw"))

    def _rpc(
        proc: subprocess.Popen[bytes],
        lock: threading.Lock,
        counter: list[int],
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        assert proc.stdin is not None
        assert proc.stdout is not None
        with lock:
            counter[0] += 1
            req_id = counter[0]
            msg = json.dumps(
                {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            )
            proc.stdin.write((msg + "\n").encode())
            proc.stdin.flush()

            streamed: list[str] = []
            while True:
                raw = proc.stdout.readline()
                if not raw:
                    raise RuntimeError(
                        "zeroclaw ACP process closed stdout unexpectedly"
                    )
                try:
                    envelope = json.loads(raw)
                except json.JSONDecodeError:
                    continue
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
                    raise RuntimeError(f"ACP error: {envelope['error']}")
                result: dict[str, Any] = envelope.get("result") or {}
                if streamed:
                    result["_streamed"] = "".join(streamed)
                return result

    async def call(task: str, ctx: dict[str, Any]) -> str | None:
        cfg_dir = _resolve_config_dir()
        proc = await asyncio.to_thread(
            subprocess.Popen,
            ["zeroclaw", "acp", "--config-dir", cfg_dir],
            env=dict(os.environ),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
        )
        lock = threading.Lock()
        counter: list[int] = [0]

        def do_rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
            return _rpc(proc, lock, counter, method, params)

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_run_zeroclaw_session, do_rpc, task),
                timeout=timeout,
            )
            return result
        finally:
            with contextlib.suppress(Exception):
                proc.terminate()
                proc.wait(timeout=5)

    return call


def _run_zeroclaw_session(
    rpc: Callable[[str, dict[str, Any]], dict[str, Any]], task: str
) -> str | None:
    sess = rpc("session/new", {})
    session_id = str(sess.get("sessionId", ""))
    result = rpc("session/prompt", {"sessionId": session_id, "prompt": task})
    with contextlib.suppress(Exception):
        rpc("session/stop", {"sessionId": session_id})
    for key in ("content", "message", "text", "_streamed"):
        val = result.get(key)
        if isinstance(val, str) and val:
            return val
    return str(result) if result else None


def custom_caller(adapter_path: str, url: str | None, timeout: float) -> CallerFn:
    """Load and delegate to a user-supplied async plugin function.

    The plugin must have the signature::

        async def fn(task: str, url: str | None, timeout: float) -> str
    """
    module_name, fn_name = adapter_path.rsplit(":", 1)
    module = importlib.import_module(module_name)
    fn: Callable[..., Coroutine[Any, Any, str]] = getattr(module, fn_name)

    async def call(task: str, ctx: dict[str, Any]) -> str | None:
        return await fn(task, url, timeout)

    return call
