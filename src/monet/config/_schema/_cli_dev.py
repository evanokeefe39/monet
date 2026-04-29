from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .._env import (
    EXA_API_KEY,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    TAVILY_API_KEY,
    ConfigError,
    read_str,
)
from ._common import _redact


class CLIDevConfig(BaseModel):
    """What ``monet dev`` / ``monet run`` / ``monet chat`` require.

    The current contract is that at least one LLM provider key is set so
    the reference agents can instantiate a model. The exact key names are
    a policy of the reference agents, not of monet itself; keeping this
    here avoids scattering the check across CLI commands.
    """

    model_config = ConfigDict(frozen=True)

    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    exa_api_key: str | None = None
    tavily_api_key: str | None = None

    @classmethod
    def load(cls) -> CLIDevConfig:
        return cls(
            gemini_api_key=read_str(GEMINI_API_KEY),
            groq_api_key=read_str(GROQ_API_KEY),
            exa_api_key=read_str(EXA_API_KEY),
            tavily_api_key=read_str(TAVILY_API_KEY),
        )

    def validate_for_boot(self) -> None:
        if not (self.gemini_api_key or self.groq_api_key):
            raise ConfigError(
                f"{GEMINI_API_KEY} or {GROQ_API_KEY}",
                None,
                "at least one LLM provider key (set it in .env or the "
                "environment before running monet dev/run/chat)",
            )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "gemini_api_key": _redact(self.gemini_api_key),
            "groq_api_key": _redact(self.groq_api_key),
            "exa_api_key": _redact(self.exa_api_key),
            "tavily_api_key": _redact(self.tavily_api_key),
        }
