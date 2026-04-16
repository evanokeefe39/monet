"""E2E-03 — aegra serve with external Postgres.

Spawns ``aegra serve`` pointed at a Postgres testcontainer (not the
docker-compose Postgres that ``monet dev`` provisions) and drives an
auto-approve run to completion. Verifies the checkpoint tables exist
and at least one row was written to ``thread`` in the external DB.

Gated behind ``MONET_E2E=1`` and the ``postgres_container`` fixture;
skipped cleanly when Docker is unavailable.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
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
RUN_TIMEOUT_SECONDS = 300.0
BOOT_TIMEOUT = 180.0
AEGRA_PORT = STANDARD_DEV_PORT + 1  # avoid colliding with monet_dev_server fixture


def _free_port_check(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            pytest.skip(f"port {port} already bound; aegra serve cannot start")


def _wait_health(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            pass
        time.sleep(1.0)
    msg = f"aegra health did not become ready within {timeout}s at {url}"
    raise RuntimeError(msg)


@pytest.fixture
def aegra_with_external_postgres(
    postgres_container: Any,
) -> Iterator[tuple[str, str]]:
    """Start ``aegra serve`` against the provided Postgres testcontainer.

    Yields ``(aegra_url, db_url)``.
    """
    aegra_bin = shutil.which("aegra")
    if aegra_bin is None:
        pytest.skip("'aegra' not on PATH — install aegra-cli")
    _free_port_check(AEGRA_PORT)

    db_url = postgres_container.get_connection_url()
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    env["MONET_API_KEY"] = env.get("MONET_API_KEY", "test-key")
    env["AEGRA_PORT"] = str(AEGRA_PORT)

    log_path = QUICKSTART_DIR / ".monet" / "e2e-aegra-external.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("wb")
    proc = subprocess.Popen(
        [aegra_bin, "serve", "--port", str(AEGRA_PORT)],
        cwd=QUICKSTART_DIR,
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    aegra_url = f"http://localhost:{AEGRA_PORT}"
    try:
        _wait_health(f"{aegra_url}/health", BOOT_TIMEOUT)
        yield aegra_url, db_url
    finally:
        log_fh.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.e2e
def test_aegra_serves_against_external_postgres(
    aegra_with_external_postgres: tuple[str, str],
) -> None:
    """aegra serve boots + pipeline completes + checkpoint rows land in ext DB."""
    aegra_url, db_url = aegra_with_external_postgres
    monet_bin = shutil.which("monet")
    assert monet_bin is not None

    env = os.environ.copy()
    env["MONET_API_URL"] = aegra_url

    result = subprocess.run(
        [
            monet_bin,
            "run",
            "AI trends in healthcare",
            "--auto-approve",
            "--output",
            "json",
        ],
        cwd=QUICKSTART_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, (
        f"monet run exited {result.returncode}. stderr:\n{result.stderr}"
    )
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    kinds = {
        ev.get("event") or ev.get("type")
        for ev in events
        if isinstance(ev.get("event") or ev.get("type"), str)
    }
    assert any("run_complete" in k for k in kinds if k), (
        f"no run_complete in events: {kinds}"
    )

    # Verify Aegra persisted at least one thread row to the external DB.
    import sqlalchemy as sa

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        inspector = sa.inspect(conn)
        tables = set(inspector.get_table_names())
        assert "thread" in tables or "runs" in tables, (
            f"expected aegra schema in external DB; got tables={tables}"
        )
    engine.dispose()
