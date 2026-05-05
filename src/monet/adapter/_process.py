from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._config import ProcessConfig


def start_process(config: ProcessConfig) -> subprocess.Popen[bytes]:
    """Start subprocess with env + workdir. stderr passes through to parent."""
    env = dict(os.environ)
    env.update(config.env)
    kwargs: dict[str, Any] = {
        "env": env,
        "stdout": sys.stdout,
        "stderr": sys.stderr,
    }
    if config.workdir:
        kwargs["cwd"] = config.workdir
    return subprocess.Popen(config.command, **kwargs)


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM with 5s grace, then SIGKILL. Windows: taskkill /F /T."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    else:
        import signal

        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
