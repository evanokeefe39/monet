from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .._env import MONET_AGENT_TIMEOUT, ConfigError, read_float


class OrchestrationConfig(BaseModel):
    """Dispatcher-side tuning. Today just the agent-timeout poll."""

    model_config = ConfigDict(frozen=True)

    agent_timeout: float = Field(default=600.0, gt=0.0)

    @classmethod
    def load(cls) -> OrchestrationConfig:
        timeout = read_float(MONET_AGENT_TIMEOUT, default=600.0)
        if timeout <= 0.0:
            raise ConfigError(
                MONET_AGENT_TIMEOUT,
                str(timeout),
                "a positive float (seconds)",
            )
        return cls(agent_timeout=timeout)

    def redacted_summary(self) -> dict[str, Any]:
        return {"agent_timeout": self.agent_timeout}
