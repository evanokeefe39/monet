from __future__ import annotations

from pathlib import Path  # noqa: TC003 — pydantic needs this at runtime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .._env import (
    GEMINI_API_KEY,
    GROQ_API_KEY,
    MONET_API_KEY,
    MONET_SERVER_URL,
    MONET_WORKER_AGENTS,
    MONET_WORKER_CONCURRENCY,
    MONET_WORKER_HEARTBEAT_INTERVAL,
    MONET_WORKER_POLL_INTERVAL,
    MONET_WORKER_POOL,
    MONET_WORKER_SHUTDOWN_TIMEOUT,
    ConfigError,
    read_float,
    read_int,
    read_path,
    read_str,
)
from ._common import _UNSET, _redact


class WorkerConfig(BaseModel):
    """Config surface for a :command:`monet worker` process.

    When ``server_url`` is set the worker runs in remote/distributed mode
    and requires ``api_key``. When it is unset, the worker runs in local
    sidecar mode and neither is required.
    """

    model_config = ConfigDict(frozen=True)

    pool: str = "local"
    concurrency: int = Field(default=10, gt=0)
    server_url: str | None = None
    api_key: str | None = None
    agents_toml: Path | None = None
    poll_interval: float = Field(default=0.1, gt=0.0)
    shutdown_timeout: float = Field(default=30.0, gt=0.0)
    heartbeat_interval: float = Field(default=30.0, gt=0.0)
    required_llm_keys: tuple[str, ...] = ()

    @classmethod
    def load(cls) -> WorkerConfig:
        return cls(
            pool=read_str(MONET_WORKER_POOL, "local") or "local",
            concurrency=read_int(MONET_WORKER_CONCURRENCY, default=10),
            server_url=read_str(MONET_SERVER_URL),
            api_key=read_str(MONET_API_KEY),
            agents_toml=read_path(MONET_WORKER_AGENTS),
            poll_interval=read_float(MONET_WORKER_POLL_INTERVAL, default=0.1),
            shutdown_timeout=read_float(MONET_WORKER_SHUTDOWN_TIMEOUT, default=30.0),
            heartbeat_interval=read_float(
                MONET_WORKER_HEARTBEAT_INTERVAL, default=30.0
            ),
        )

    def with_required_llm_keys(self, keys: tuple[str, ...]) -> WorkerConfig:
        """Return a copy carrying the LLM-key names this worker needs."""
        return self.model_copy(update={"required_llm_keys": keys})

    def validate_for_boot(self) -> None:
        if self.server_url and not self.api_key:
            raise ConfigError(
                MONET_API_KEY,
                None,
                f"a non-empty bearer token (required when {MONET_SERVER_URL} is set)",
            )
        if self.required_llm_keys:
            import os as _os

            if not any(_os.environ.get(k) for k in self.required_llm_keys):
                raise ConfigError(
                    " or ".join(self.required_llm_keys),
                    None,
                    "at least one LLM provider key set in the worker "
                    f"environment (e.g. {GEMINI_API_KEY} or "
                    f"{GROQ_API_KEY})",
                )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "pool": self.pool,
            "concurrency": self.concurrency,
            "server_url": self.server_url or _UNSET,
            "api_key": _redact(self.api_key),
            "agents_toml": (str(self.agents_toml) if self.agents_toml else _UNSET),
            "poll_interval": self.poll_interval,
            "shutdown_timeout": self.shutdown_timeout,
            "heartbeat_interval": self.heartbeat_interval,
            "required_llm_keys": list(self.required_llm_keys),
        }
