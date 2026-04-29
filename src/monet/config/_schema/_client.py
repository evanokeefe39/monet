from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .._env import MONET_API_KEY, MONET_DATA_PLANE_URL, MONET_SERVER_URL, read_str
from .._load import read_toml_section
from ._common import _DEFAULT_SERVER_URL, _redact


class ClientConfig(BaseModel):
    """Config surface for :class:`monet.client.MonetClient`."""

    model_config = ConfigDict(frozen=True)

    server_url: str = _DEFAULT_SERVER_URL
    api_key: str | None = None
    data_plane_url: str | None = None

    @classmethod
    def load(cls) -> ClientConfig:
        planes = read_toml_section("planes")
        return cls(
            server_url=(
                read_str(MONET_SERVER_URL, _DEFAULT_SERVER_URL) or _DEFAULT_SERVER_URL
            ),
            api_key=read_str(MONET_API_KEY),
            data_plane_url=(
                read_str(MONET_DATA_PLANE_URL) or (planes.get("data_url") or None)
            ),
        )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "server_url": self.server_url,
            "api_key": _redact(self.api_key),
            "data_plane_url": self.data_plane_url,
        }
