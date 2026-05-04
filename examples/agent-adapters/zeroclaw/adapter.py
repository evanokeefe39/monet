#!/usr/bin/env python3
"""Monet /task adapter wrapping ZeroClaw.

Starts ZeroClaw on ZEROCLAW_PORT (default 3002) as a subprocess, then
serves the monet /task protocol on ADAPTER_PORT (default 8080).

Protocol translation:
    POST /task {task_id, payload: {task, ...}}
      -> POST http://localhost:3002/v1/chat/completions (OpenAI-compat)
      <- {choices: [{message: {content: "response"}}]}
      -> {output: "response", success: true}
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ZEROCLAW_PORT = int(os.environ.get("ZEROCLAW_PORT", "3002"))
ADAPTER_PORT = int(os.environ.get("ADAPTER_PORT", "8080"))
_ZC_BASE = f"http://localhost:{ZEROCLAW_PORT}"
_ZC_CHAT = f"{_ZC_BASE}/v1/chat/completions"

_zc_proc: subprocess.Popen[bytes] | None = None


def _build_zc_env() -> dict[str, str]:
    env = dict(os.environ)
    # ZeroClaw with provider=openai reads OPENAI_API_KEY.
    if "NVIDIA_NIM_API_KEY" in env and "OPENAI_API_KEY" not in env:
        env["OPENAI_API_KEY"] = env["NVIDIA_NIM_API_KEY"]
    return env


def _start_zeroclaw() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ["zeroclaw", "serve", "--config", "/etc/zeroclaw/config.toml"],
        env=_build_zc_env(),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _zc_healthy() -> bool:
    """TCP probe — ZeroClaw has no /health endpoint."""
    try:
        with socket.create_connection(("localhost", ZEROCLAW_PORT), timeout=2):
            return True
    except OSError:
        return False


def _wait_zc_ready(timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _zc_healthy():
            return
        time.sleep(1.0)
    raise RuntimeError(f"ZeroClaw did not become ready within {timeout_s}s")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/health":
            ok = _zc_healthy()
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
            self.send_response(400)
            self.end_headers()
            return

        task_payload: dict[str, object] = payload.get("payload", {})
        message: str = str(task_payload.get("task") or task_payload.get("command", ""))

        zc_body = json.dumps(
            {
                "model": "deepseek-ai/deepseek-v4-pro",
                "messages": [{"role": "user", "content": message}],
            }
        ).encode()
        req = urllib.request.Request(
            _ZC_CHAT,
            data=zc_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                zc_resp = json.loads(r.read())
            content: str = zc_resp["choices"][0]["message"]["content"]
            result = json.dumps({"output": content, "success": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(result)
        except (urllib.error.HTTPError, KeyError, IndexError) as exc:
            error = json.dumps({"error": str(exc)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(error)
        except Exception as exc:
            error = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(error)


if __name__ == "__main__":
    _zc_proc = _start_zeroclaw()
    print(
        f"ZeroClaw started (pid={_zc_proc.pid}), waiting for readiness...", flush=True
    )
    _wait_zc_ready()
    print(f"ZeroClaw ready. Adapter listening on :{ADAPTER_PORT}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", ADAPTER_PORT), _Handler)
    try:
        server.serve_forever()
    finally:
        _zc_proc.terminate()
