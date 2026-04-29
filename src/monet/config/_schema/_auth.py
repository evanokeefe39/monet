from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .._env import MONET_API_KEY, ConfigError, read_str
from ._common import _redact


class AuthConfig(BaseModel):
    """Bearer-token secret for the FastAPI server."""

    model_config = ConfigDict(frozen=True)

    api_key: str | None = None

    @classmethod
    def load(cls) -> AuthConfig:
        return cls(api_key=read_str(MONET_API_KEY))

    def validate_for_boot(self, *, required: bool = False) -> None:
        """Validate the bearer token.

        When *required* is ``True`` (typically distributed/production
        mode), a missing key raises :exc:`ConfigError` at boot — this
        prevents a server from booting green and 500-ing on the first
        authenticated call.
        """
        if required and not self.api_key:
            raise ConfigError(
                MONET_API_KEY,
                None,
                "a non-empty bearer token (required when the server is "
                "enforcing auth — set it in the environment before boot)",
            )

    def redacted_summary(self) -> dict[str, Any]:
        return {"api_key": _redact(self.api_key)}
