"""DevServer: shared boot+isolation helper for compat harness tests.

Context manager that boots ``monet dev`` in a subprocess on a probed-free
port, polls ``/health`` until ready, and guarantees teardown. Isolation is
best-effort: HOME is redirected to a tmpdir so ``~/.monet/state.json`` of
the developer's live session stays untouched.

Also callable as ``python -m tests.compat._server --port N [--keep]``
for manual harness development.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from types import TracebackType


def _free_port() -> int:
    """Probe an ephemeral port and release it. Caller races OS for reuse."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


DEFAULT_CWD = Path(__file__).resolve().parents[2] / "examples" / "chat-default"


class DevServer:
    """Spawns ``monet dev`` on an isolated port and tears it down on exit."""

    def __init__(
        self,
        *,
        port: int | None = None,
        cwd: Path | None = None,
        api_key: str = "compat-test-key",
        boot_timeout: float = 30.0,
        keep_tmp: bool = False,
    ) -> None:
        self.port = port or _free_port()
        self.cwd = (cwd or DEFAULT_CWD).resolve()
        self.api_key = api_key
        self.boot_timeout = boot_timeout
        self.keep_tmp = keep_tmp

        self._tmp = tempfile.TemporaryDirectory(prefix="monet-compat-")
        self._home = Path(self._tmp.name)
        self._proc: subprocess.Popen[bytes] | None = None

    # ── Public surface ─────────────────────────────────────────────

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        env = os.environ.copy()
        env["HOME"] = str(self._home)
        env["USERPROFILE"] = str(self._home)
        env["MONET_API_KEY"] = self.api_key
        env["MONET_SERVER_URL"] = self.url
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        cmd = [
            sys.executable,
            "-m",
            "monet",
            "dev",
            "--port",
            str(self.port),
            "--verbose",
        ]
        # Fall back to `uv run monet dev` if `python -m monet` isn't wired.
        if not _monet_module_runnable():
            cmd = [
                "uv",
                "run",
                "monet",
                "dev",
                "--port",
                str(self.port),
                "--verbose",
            ]

        self._proc = subprocess.Popen(
            cmd,
            cwd=str(self.cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._wait_ready()

    def _wait_ready(self) -> None:
        assert self._proc is not None
        deadline = time.monotonic() + self.boot_timeout
        url = f"{self.url}/health"
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                out = b""
                if self._proc.stdout is not None:
                    out = self._proc.stdout.read() or b""
                raise RuntimeError(
                    f"monet dev exited early (code {self._proc.returncode}):\n"
                    f"{out.decode(errors='replace')}"
                )
            try:
                with urllib.request.urlopen(url, timeout=1.0) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                pass
            time.sleep(0.5)
        self.stop()
        raise TimeoutError(
            f"monet dev did not become ready within {self.boot_timeout}s"
        )

    def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        if proc.poll() is None:
            if sys.platform == "win32":
                proc.terminate()  # TerminateProcess — no SIGTERM on Windows
            else:
                proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)
        if not self.keep_tmp:
            self._tmp.cleanup()

    # ── Context manager ────────────────────────────────────────────

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()


def _monet_module_runnable() -> bool:
    """Return True if ``python -m monet`` resolves in the current interpreter."""
    try:
        import monet  # noqa: F401

        return True
    except ImportError:
        return False


def _main() -> None:
    ap = argparse.ArgumentParser(prog="tests.compat._server")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--cwd", type=Path, default=None)
    ap.add_argument("--keep", action="store_true", help="Preserve tmpdir on exit")
    args = ap.parse_args()

    with DevServer(port=args.port, cwd=args.cwd, keep_tmp=args.keep) as s:
        print(f"monet dev ready at {s.url} (api_key={s.api_key})", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("shutting down...", flush=True)


if __name__ == "__main__":
    _main()
