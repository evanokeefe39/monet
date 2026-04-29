from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from ._artifacts import ArtifactsConfig
from ._auth import AuthConfig
from ._chat import ChatConfig
from ._observability import ObservabilityConfig
from ._orchestration import OrchestrationConfig
from ._queue import QueueConfig


class ServerConfig(BaseModel):
    """Full config surface consumed by a server process.

    Composes :class:`AuthConfig`, :class:`QueueConfig`,
    :class:`ArtifactsConfig`, :class:`ObservabilityConfig`,
    :class:`OrchestrationConfig`, and :class:`ChatConfig`.
    """

    model_config = ConfigDict(frozen=True)

    auth: AuthConfig
    queue: QueueConfig
    artifacts: ArtifactsConfig
    observability: ObservabilityConfig
    orchestration: OrchestrationConfig
    chat: ChatConfig

    @classmethod
    def load(cls) -> ServerConfig:
        return cls(
            auth=AuthConfig.load(),
            queue=QueueConfig.load(),
            artifacts=ArtifactsConfig.load(),
            observability=ObservabilityConfig.load(),
            orchestration=OrchestrationConfig.load(),
            chat=ChatConfig.load(),
        )

    def validate_for_boot(self) -> None:
        """Validate preconditions that must hold before the server starts.

        Raises :exc:`ConfigError` on the first violation. The
        :class:`AuthConfig` check is strict only in distributed mode —
        local monolith dev boots don't need a bearer token to be useful,
        but a production distributed server with no ``MONET_API_KEY``
        would boot green and 500 on the first authenticated call, which
        is exactly the silent-failure class this module exists to
        prevent.
        """
        self.auth.validate_for_boot(required=self.artifacts.distributed)
        self.queue.validate_for_boot()
        self.artifacts.validate_for_boot()
        self.chat.validate_for_boot()

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "auth": self.auth.redacted_summary(),
            "queue": self.queue.redacted_summary(),
            "artifacts": self.artifacts.redacted_summary(),
            "observability": self.observability.redacted_summary(),
            "orchestration": self.orchestration.redacted_summary(),
            "chat": self.chat.redacted_summary(),
        }
