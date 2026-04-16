"""End-to-end test fixtures.

These tests exercise a real ``monet dev`` subprocess, which provisions
Postgres via Docker and serves the compiled graphs through Aegra.
Running them requires:

- Docker running locally
- ``MONET_E2E=1`` set in the environment
- LLM provider credentials (``GEMINI_API_KEY`` or ``GROQ_API_KEY``)
  reachable from the example working directory's ``.env``

Tests are skipped by default so the standard ``pytest`` run stays fast
and hermetic. Invoke explicitly with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from monet._ports import STANDARD_DEV_PORT

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
QUICKSTART_DIR = REPO_ROOT / "examples" / "quickstart"
SERVER_LOG_FILE = QUICKSTART_DIR / ".monet" / "e2e-dev-server.log"
HEALTH_URL = f"http://localhost:{STANDARD_DEV_PORT}/health"
# Cold Postgres container creation + Aegra startup can exceed 90s on
# Windows. Allow plenty of headroom; the fast-boot case still yields
# in a few seconds.
BOOT_TIMEOUT_SECONDS = 180.0
HEALTH_POLL_INTERVAL = 1.0


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip ``e2e`` tests unless ``MONET_E2E=1``."""
    if os.environ.get("MONET_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="E2E disabled — set MONET_E2E=1 to enable")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


def _wait_for_health(timeout: float) -> None:
    """Poll ``/health`` until 200 or timeout. Raises on timeout."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(HEALTH_URL, timeout=2.0)
            if resp.status_code == 200:
                return
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            last_error = exc
        time.sleep(HEALTH_POLL_INTERVAL)
    msg = (
        f"monet dev server did not become healthy within {timeout}s "
        f"(last error: {last_error})"
    )
    raise RuntimeError(msg)


@pytest.fixture(scope="session")
def monet_dev_server() -> Iterator[str]:
    """Start ``monet dev`` in the quickstart example; yield its URL.

    Session-scoped so the Postgres container and Aegra process are
    reused across all e2e tests in a run.
    """
    if not QUICKSTART_DIR.exists():
        pytest.skip(f"quickstart example missing at {QUICKSTART_DIR}")
    monet_bin = shutil.which("monet")
    if monet_bin is None:
        pytest.skip("'monet' script not on PATH — install the package first")

    # Route stdout to a file so (a) the kernel pipe buffer cannot fill
    # and block the Aegra subprocess mid-boot, and (b) the server's log
    # is inspectable when a test fails.
    SERVER_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_fh = SERVER_LOG_FILE.open("wb")
    proc = subprocess.Popen(
        [monet_bin, "dev"],
        cwd=QUICKSTART_DIR,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    try:
        try:
            _wait_for_health(BOOT_TIMEOUT_SECONDS)
        except RuntimeError as exc:
            tail = _read_tail(SERVER_LOG_FILE, max_bytes=4096)
            msg = f"{exc}\n\n--- monet dev log tail ---\n{tail}"
            raise RuntimeError(msg) from None
        yield f"http://localhost:{STANDARD_DEV_PORT}"
    finally:
        log_fh.close()
        subprocess.run(
            [monet_bin, "dev", "down"],
            cwd=QUICKSTART_DIR,
            check=False,
            timeout=30,
        )
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _read_tail(path: Path, max_bytes: int = 4096) -> str:
    """Return the last ``max_bytes`` of ``path`` as text, or a hint."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return f"(could not read {path}: {exc})"


def _skip_if_no_docker() -> None:
    """Skip the current test when Docker is unreachable.

    Testcontainers fails with a noisy ``DockerException`` when Docker
    Desktop is not running; a clean skip keeps cross-platform runs sane.
    """
    try:
        import docker  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("docker python client missing (install testcontainers extras)")
    try:
        docker.from_env().ping()
    except Exception as exc:  # docker.errors.DockerException or network error
        pytest.skip(f"Docker daemon unreachable: {exc}")


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[Any]:
    """Session-scoped Postgres testcontainer. Yields the container handle.

    Callers read ``.get_connection_url()`` for the SQLAlchemy URL.
    """
    _skip_if_no_docker()
    from testcontainers.postgres import (
        PostgresContainer,  # type: ignore[import-untyped]
    )

    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Iterator[Any]:
    """Session-scoped Redis testcontainer. Yields the container handle.

    Callers read ``.get_container_host_ip()`` and ``.get_exposed_port(6379)``.
    """
    _skip_if_no_docker()
    from testcontainers.redis import RedisContainer  # type: ignore[import-untyped]

    with RedisContainer("redis:7-alpine") as container:
        yield container
