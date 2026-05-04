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


# ---------------------------------------------------------------------------
# Agent adapter image build fixtures (used by agent-integration E2E tests)
# ---------------------------------------------------------------------------

ADAPTERS_DIR = REPO_ROOT / "examples" / "agent-adapters"
CLAW_BOT_PI_SRC = Path.home() / "repos" / "claw-bot-evals" / "pi-agents"


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file; return key/value pairs (no shell expansion)."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        v = value.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        result[key.strip()] = v
    return result


def _agent_env() -> dict[str, str]:
    """Env vars to inject into agent containers (API keys, LLM routing)."""
    env_file = REPO_ROOT / ".env"
    keys = _load_env_file(env_file)
    result: dict[str, str] = {}
    for k in (
        "NVIDIA_NIM_API_KEY",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "TAVILY_API_KEY",
        "EXA_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    ):
        if k in os.environ:
            result[k] = os.environ[k]
        elif k in keys:
            result[k] = keys[k]
    # Alias for zeroclaw's nvidia provider which reads NVIDIA_API_KEY.
    if "NVIDIA_NIM_API_KEY" in result and "NVIDIA_API_KEY" not in result:
        result["NVIDIA_API_KEY"] = result["NVIDIA_NIM_API_KEY"]
    return result


def _build_pi_image(tag: str, adapter_dir: Path) -> str:
    """Build a pi-based adapter image; return the image tag."""
    _skip_if_no_docker()
    if not CLAW_BOT_PI_SRC.exists():
        pytest.skip(f"Pi source not found at {CLAW_BOT_PI_SRC}")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ctx = Path(tmp)
        # Adapter files at root of build context.
        for f in adapter_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, ctx / f.name)
        # Pi source at pi-src/ (referenced by Dockerfile COPY pi-src/...).
        shutil.copytree(CLAW_BOT_PI_SRC, ctx / "pi-src")
        subprocess.run(
            ["docker", "build", "-t", tag, str(ctx)],
            check=True,
            timeout=600,
        )
    return tag


@pytest.fixture(scope="session")
def pi_agent_image() -> str:
    """Build monet-e2e/pi-agent:latest; return the tag."""
    return _build_pi_image("monet-e2e/pi-agent:latest", ADAPTERS_DIR / "pi")


@pytest.fixture(scope="session")
def pi_gateway_agent_image() -> str:
    """Build monet-e2e/pi-gateway-agent:latest; return the tag."""
    return _build_pi_image(
        "monet-e2e/pi-gateway-agent:latest", ADAPTERS_DIR / "pi-gateway"
    )


@pytest.fixture(scope="session")
def zeroclaw_agent_image() -> str:
    """Build monet-e2e/zeroclaw-agent:latest; return the tag."""
    _skip_if_no_docker()
    tag = "monet-e2e/zeroclaw-agent:latest"
    subprocess.run(
        ["docker", "build", "-t", tag, str(ADAPTERS_DIR / "zeroclaw")],
        check=True,
        timeout=600,
    )
    return tag


@pytest.fixture(scope="session")
def agent_env() -> dict[str, str]:
    """API key env vars to inject into agent containers."""
    return _agent_env()


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


# ---------------------------------------------------------------------------
# Embedded gateway fixture (used by T5 gateway roundtrip test)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def embedded_gateway() -> Iterator[dict[str, Any]]:
    """Start the monet gateway on a random local port.

    Yields a dict with:
        host_url    — reachable from the test process (http://localhost:{port})
        docker_url  — reachable from inside Docker containers
        signing_key — JWT signing key used by the gateway
        ctx         — GatewayContext (for post-hoc artifact inspection)
    """
    import socket
    import threading
    import time

    import uvicorn

    from monet.artifacts._memory import InMemoryArtifactClient
    from monet.gateway import DEV_SIGNING_KEY, GatewayContext, create_gateway_app

    class _NullProgressWriter:
        async def record(self, run_id: str, event: Any) -> int:
            return 0

    # Find a free port.
    with socket.socket() as sock:
        sock.bind(("", 0))
        port = sock.getsockname()[1]

    ctx = GatewayContext(
        artifact_client=InMemoryArtifactClient(),
        progress_writer=_NullProgressWriter(),
        signing_key=DEV_SIGNING_KEY,
    )
    app = create_gateway_app(ctx)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to become ready.
    deadline = time.monotonic() + 10.0
    import httpx as _httpx

    while time.monotonic() < deadline:
        try:
            _httpx.get(f"http://localhost:{port}/health", timeout=1.0)
            break
        except Exception:
            time.sleep(0.2)

    # On Windows/Mac Docker can reach the host via host.docker.internal.
    docker_url = f"http://host.docker.internal:{port}"

    yield {
        "host_url": f"http://localhost:{port}",
        "docker_url": docker_url,
        "signing_key": DEV_SIGNING_KEY,
        "ctx": ctx,
    }

    server.should_exit = True
    thread.join(timeout=5)
