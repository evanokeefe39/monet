"""Typed configuration schemas per deployable unit.

Each deployable unit (server, worker, client, CLI-dev, orchestration
dispatcher, artifact store, observability) has its own pydantic schema
composed from the small per-concern models below:

- :class:`ObservabilityConfig` — tracing targets.
- :class:`ArtifactsConfig` — artifact store root + distributed flag.
- :class:`QueueConfig` — task queue backend + credentials.
- :class:`AuthConfig` — bearer-token secret.
- :class:`OrchestrationConfig` — dispatch-side tuning (agent timeout).
- :class:`ServerConfig` — composes everything a server process needs.
- :class:`WorkerConfig` — what a worker process needs to claim + execute.
- :class:`ClientConfig` — what :class:`monet.client.MonetClient` needs.
- :class:`CLIDevConfig` — what ``monet dev`` / ``monet run`` require.

Each schema exposes:

- ``load()`` — classmethod that reads env + ``monet.toml`` and returns a
  populated instance. Raises :exc:`ConfigError` only on parse failures
  (malformed value); missing values fall through to defaults.
- ``validate_for_boot()`` — runs cross-field preconditions that must
  hold before a process can start. Raises :exc:`ConfigError` naming the
  first violation. Called once at process startup — never per-request.
- ``redacted_summary()`` — returns a dict suitable for INFO-level boot
  logging. Secrets are shown as ``"set"`` / ``"unset"`` but never as
  their raw value.
"""

from __future__ import annotations

from ._artifacts import ArtifactsConfig
from ._auth import AuthConfig
from ._chat import ChatConfig
from ._cli_dev import CLIDevConfig
from ._client import ClientConfig
from ._common import QueueBackend
from ._observability import ObservabilityConfig
from ._orchestration import OrchestrationConfig
from ._planes import PlanesConfig, ProgressBackend, ProgressConfig
from ._queue import QueueConfig
from ._server import ServerConfig
from ._worker import WorkerConfig

__all__ = [
    "ArtifactsConfig",
    "AuthConfig",
    "CLIDevConfig",
    "ChatConfig",
    "ClientConfig",
    "ObservabilityConfig",
    "OrchestrationConfig",
    "PlanesConfig",
    "ProgressBackend",
    "ProgressConfig",
    "QueueBackend",
    "QueueConfig",
    "ServerConfig",
    "WorkerConfig",
]
