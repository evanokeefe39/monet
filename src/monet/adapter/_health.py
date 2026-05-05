from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    import subprocess

    from ._config import AdapterConfig

HealthCheck = Callable[[], Awaitable[bool]]


@dataclass
class HealthCascade:
    checks: list[HealthCheck] = field(default_factory=list)


async def _try_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(url)
            return r.status_code < 500
    except Exception:
        return False


async def _try_tcp(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        _, writer = await asyncio.open_connection(host, port)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def _proc_alive(proc: subprocess.Popen[bytes]) -> Callable[[], Awaitable[bool]]:
    async def _check() -> bool:
        return proc.poll() is None

    return _check


def _base_url(url: str) -> str:
    """Strip path — keep scheme://host:port only."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def build_cascade(
    config: AdapterConfig, proc: subprocess.Popen[bytes] | None = None
) -> HealthCascade:
    checks: list[HealthCheck] = []

    if config.type == "openai":
        base = _base_url(config.url)
        health_url = f"{base}/health"
        models_url = f"{base}/v1/models"
        checks.append(lambda: _try_http(health_url))
        checks.append(lambda: _try_http(models_url))
        checks.append(lambda: _try_tcp(base))

    elif config.type == "http":
        base = _base_url(config.url)
        if config.health:
            health_path = config.health
            checks.append(lambda: _try_http(f"{base}{health_path}"))
        checks.append(lambda: _try_tcp(config.url))

    elif config.type in ("stdio", "plugin"):
        if proc is not None:
            checks.append(_proc_alive(proc))
        else:

            async def _always_true() -> bool:
                return True

            checks.append(_always_true)

    return HealthCascade(checks=checks)


async def check_health(cascade: HealthCascade) -> bool:
    for check in cascade.checks:
        if await check():
            return True
    return False


async def wait_healthy(cascade: HealthCascade, timeout: int) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await check_health(cascade):
            return
        await asyncio.sleep(1.0)
    raise TimeoutError(f"Agent not ready within {timeout}s")
