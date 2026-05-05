#!/usr/bin/env python3
"""Monet /task adapter wrapping Pi + data plane gateway artifact write.

Same as the Pi adapter, but also writes the agent's output as an artifact
to the monet data plane gateway before returning. Reads MONET_GATEWAY_URL
and MONET_TOKEN from the environment (injected by the worker).

Artifact key: research_output
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PI_PORT = int(os.environ.get("PI_PORT", "9000"))
ADAPTER_PORT = int(os.environ.get("ADAPTER_PORT", "8080"))
_PI_BASE = f"http://localhost:{PI_PORT}"
_PI_CHAT = f"{_PI_BASE}/chat?stream=false"
_PI_HEALTH = f"{_PI_BASE}/health"

_pi_proc: subprocess.Popen[bytes] | None = None


def _build_pi_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PORT"] = str(PI_PORT)
    # Route via NVIDIA NIM (OpenAI-compatible) when key is available — NIM has
    # generous rate limits that fit Pi's large system prompt. Fall back to Groq.
    # LLM_PROVIDER / LLM_MODEL env vars override these defaults at deploy time.
    if "NVIDIA_NIM_API_KEY" in env and not env.get("LLM_PROVIDER"):
        env["LLM_PROVIDER"] = "openai"
        env["LLM_MODEL"] = "meta/llama-3.3-70b-instruct"
        env["OPENAI_BASE_URL"] = "https://integrate.api.nvidia.com/v1"
        env["OPENAI_API_KEY"] = env["NVIDIA_NIM_API_KEY"]
    else:
        env.setdefault("LLM_PROVIDER", "groq")
        env.setdefault("LLM_MODEL", "llama-3.3-70b-versatile")
    return env


def _start_pi() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ["npx", "tsx", "server.ts"],
        cwd="/pi",
        env=_build_pi_env(),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _pi_healthy() -> bool:
    try:
        with urllib.request.urlopen(_PI_HEALTH, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _wait_pi_ready(timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _pi_healthy():
            return
        time.sleep(1.0)
    raise RuntimeError(f"Pi did not become ready within {timeout_s}s")


def _write_artifact(task_id: str, key: str, content: str) -> None:
    gw_url = os.environ.get("MONET_GATEWAY_URL", "")
    token = os.environ.get("MONET_TOKEN", "")
    if not gw_url or not token:
        return
    url = f"{gw_url.rstrip('/')}/artifacts/{task_id}/{key}"
    req = urllib.request.Request(
        url,
        data=content.encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as exc:
        print(f"artifact write failed: {exc}", file=sys.stderr)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/health":
            ok = _pi_healthy()
            body = b'{"ok":true}' if ok else b'{"ok":false}'
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/task":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            error = json.dumps(
                {"error": "invalid JSON", "error_code": "INVALID_REQUEST"}
            ).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(error)
            return

        task_id: str = payload.get("task_id", "")
        task_payload: dict[str, object] = payload.get("payload", {})
        message: str = str(task_payload.get("task") or task_payload.get("command", ""))

        pi_body = json.dumps({"message": message, "session_id": task_id}).encode()
        req = urllib.request.Request(
            _PI_CHAT,
            data=pi_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                pi_resp = json.loads(r.read())
            response_text: str = pi_resp.get("message", "")
            # Write artifact to gateway before returning.
            _write_artifact(task_id, "research_output", response_text)
            result = json.dumps({"output": response_text, "success": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(result)
        except urllib.error.HTTPError as exc:
            error = json.dumps(
                {"error": f"Pi returned {exc.code}", "error_code": "UPSTREAM_ERROR"}
            ).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(error)
        except Exception as exc:
            error = json.dumps(
                {"error": str(exc), "error_code": "AGENT_ERROR"}
            ).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(error)


if __name__ == "__main__":
    _pi_proc = _start_pi()
    print(f"Pi started (pid={_pi_proc.pid}), waiting for readiness...", flush=True)
    _wait_pi_ready()
    print(f"Pi ready. Gateway adapter listening on :{ADAPTER_PORT}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", ADAPTER_PORT), _Handler)
    try:
        server.serve_forever()
    finally:
        _pi_proc.terminate()
