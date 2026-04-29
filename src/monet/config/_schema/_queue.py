from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .._env import (
    MONET_QUEUE_BACKEND,
    MONET_QUEUE_COMPLETION_TTL,
    MONET_QUEUE_CUSTOM_BACKEND,
    MONET_QUEUE_LEASE_TTL,
    MONET_QUEUE_RECLAIM_INTERVAL,
    REDIS_URI,
    ConfigError,
    read_enum,
    read_float,
    read_int,
    read_str,
)
from ._common import _QUEUE_BACKENDS, _UNSET, QueueBackend, _redact


class QueueConfig(BaseModel):
    """Task queue backend + credentials.

    ``validate_for_boot`` is where we fail an operator-visible error on
    typos like ``MONET_QUEUE_BACKEND=redi`` or missing credentials.
    Memory backend is for tests and single-process development only; it
    is rejected at boot whenever ``REDIS_URI`` is set, which guarantees
    that a deployed server cannot silently drop to in-memory storage.
    """

    model_config = ConfigDict(frozen=True)

    backend: QueueBackend = "memory"
    custom_backend: str | None = None
    redis_uri: str | None = None
    work_stream_maxlen: int | None = None
    redis_pool_size: int = 20
    push_dispatch_timeout: float = 10.0
    lease_ttl_seconds: int = 300
    reclaim_interval_seconds: int = 30
    completion_ttl_seconds: float = 600.0

    @classmethod
    def load(cls) -> QueueConfig:
        backend_raw = read_enum(MONET_QUEUE_BACKEND, _QUEUE_BACKENDS, default="memory")
        backend: QueueBackend = (
            backend_raw if backend_raw is not None else "memory"  # type: ignore[assignment]
        )
        return cls(
            backend=backend,
            custom_backend=read_str(MONET_QUEUE_CUSTOM_BACKEND),
            redis_uri=read_str(REDIS_URI),
            lease_ttl_seconds=read_int(MONET_QUEUE_LEASE_TTL, default=300),
            reclaim_interval_seconds=read_int(MONET_QUEUE_RECLAIM_INTERVAL, default=30),
            completion_ttl_seconds=read_float(
                MONET_QUEUE_COMPLETION_TTL, default=600.0
            ),
        )

    def validate_for_boot(self) -> None:
        if self.custom_backend is not None:
            from .._resolve import validate_dotted_path

            validate_dotted_path(self.custom_backend, MONET_QUEUE_CUSTOM_BACKEND)
            return
        if self.backend == "redis" and not self.redis_uri:
            raise ConfigError(
                REDIS_URI,
                None,
                f"a Redis URI (required when {MONET_QUEUE_BACKEND}=redis)",
            )
        if self.backend == "memory" and self.redis_uri:
            raise ConfigError(
                MONET_QUEUE_BACKEND,
                self.backend,
                "the memory backend to be disabled when REDIS_URI is set "
                f"(set {MONET_QUEUE_BACKEND}=redis or unset REDIS_URI)",
            )
        if self.lease_ttl_seconds <= 0:
            raise ConfigError(
                MONET_QUEUE_LEASE_TTL,
                str(self.lease_ttl_seconds),
                "a positive integer (seconds)",
            )
        if self.reclaim_interval_seconds <= 0:
            raise ConfigError(
                MONET_QUEUE_RECLAIM_INTERVAL,
                str(self.reclaim_interval_seconds),
                "a positive integer (seconds)",
            )
        if self.lease_ttl_seconds < 2 * self.reclaim_interval_seconds:
            raise ConfigError(
                MONET_QUEUE_LEASE_TTL,
                str(self.lease_ttl_seconds),
                f"at least 2x {MONET_QUEUE_RECLAIM_INTERVAL} "
                f"({2 * self.reclaim_interval_seconds}s) so the sweeper has "
                "time to run before entries expire",
            )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "custom_backend": self.custom_backend or _UNSET,
            "redis_uri": _redact(self.redis_uri),
            "work_stream_maxlen": self.work_stream_maxlen,
            "redis_pool_size": self.redis_pool_size,
            "push_dispatch_timeout": self.push_dispatch_timeout,
            "lease_ttl_seconds": self.lease_ttl_seconds,
            "reclaim_interval_seconds": self.reclaim_interval_seconds,
            "completion_ttl_seconds": self.completion_ttl_seconds,
        }
