#!/usr/bin/env python3
"""Monet /task adapter wrapping ZeroClaw via ACP (JSON-RPC 2.0 over stdio).

Starts ZeroClaw ACP server as a subprocess on startup, then serves the
monet /task protocol on ADAPTER_PORT (default 8080).

Protocol translation:
    POST /task {task_id, payload: {task, ...}}
      -> session/new + session/prompt over ACP stdio
      <- JSON-RPC result / streaming notifications
      -> {output: "response", success: true}
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADAPTER_PORT = int(os.environ.get("ADAPTER_PORT", "8080"))

_zc_proc: subprocess.Popen[bytes] | None = None
_lock = threading.Lock()
_req_id = 0


def _build_zc_env() -> dict[str, str]:
    return dict(os.environ)


def _next_id() -> int:
    global _req_id
    _req_id += 1
    return _req_id


def _rpc(method: str, params: dict[str, object]) -> dict[str, object]:
    """Send one JSON-RPC request; return result dict.

    Reads stdout line-by-line, skipping notifications (no id field),
    accumulating streamed text chunks, until the matching id arrives.
    """
    assert _zc_proc is not None
    req_id = _next_id()
    msg = json.dumps(
        {"jsonrpc": "2.0", "method": method, "id": req_id, "params": params}
    )
    _zc_proc.stdin.write((msg + "\n").encode())  # type: ignore[union-attr]
    _zc_proc.stdin.flush()  # type: ignore[union-attr]

    streamed: list[str] = []
    while True:
        raw = _zc_proc.stdout.readline()  # type: ignore[union-attr]
        if not raw:
            raise RuntimeError("zeroclaw ACP process exited unexpectedly")
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Notification (no id) — streaming chunk from session/prompt
        if "id" not in envelope:
            p = envelope.get("params", {})
            for key in ("chunk", "content", "text", "delta"):
                if key in p and isinstance(p[key], str):
                    streamed.append(p[key])
                    break
            continue

        # Response to our request
        if envelope.get("id") != req_id:
            continue
        if "error" in envelope:
            raise RuntimeError(f"ACP error: {envelope['error']}")
        result: dict[str, object] = envelope.get("result") or {}
        if streamed:
            result["_streamed"] = "".join(streamed)
        return result


def _run_task(message: str) -> str:
    """Open a fresh ACP session, prompt it, return the text response."""
    with _lock:
        sess = _rpc("session/new", {})
        session_id = str(sess.get("sessionId", ""))
        result = _rpc("session/prompt", {"sessionId": session_id, "prompt": message})
        with contextlib.suppress(Exception):
            _rpc("session/stop", {"sessionId": session_id})
    # Extract content from result or accumulated streaming chunks
    for key in ("content", "message", "text", "_streamed"):
        val = result.get(key)
        if isinstance(val, str) and val:
            return val
    # Fallback: nested content array (same shape as OpenAI)
    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        return str(choices[0].get("message", {}).get("content", ""))
    return str(result)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/health":
            alive = _zc_proc is not None and _zc_proc.poll() is None
            body = b'{"ok":true}' if alive else b'{"ok":false}'
            self.send_response(200 if alive else 503)
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

        try:
            output = _run_task(message)
            result = json.dumps({"output": output, "success": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(result)
        except Exception as exc:
            error = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(error)


def _start_acp() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ["zeroclaw", "acp", "--config-dir", "/etc/zeroclaw"],
        env=_build_zc_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
    )


if __name__ == "__main__":
    _zc_proc = _start_acp()
    print(f"ZeroClaw ACP started (pid={_zc_proc.pid})", flush=True)
    # Initialize the ACP session
    _rpc("initialize", {})
    print(f"ZeroClaw ACP ready. Adapter listening on :{ADAPTER_PORT}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", ADAPTER_PORT), _Handler)
    try:
        server.serve_forever()
    finally:
        _zc_proc.terminate()
