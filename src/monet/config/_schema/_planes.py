from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from .._env import (
    MONET_DATA_PLANE_URL,
    MONET_PROGRESS_BACKEND,
    MONET_PROGRESS_CUSTOM_BACKEND,
    MONET_PROGRESS_DSN,
    ConfigError,
    read_enum,
    read_str,
)
from .._load import read_toml_section
from ._common import _UNSET

_PROGRESS_BACKENDS = ("postgres", "sqlite")


class ProgressBackend(StrEnum):
    """Supported progress event store backends."""

    POSTGRES = "postgres"
    SQLITE = "sqlite"


class ProgressConfig(BaseModel):
    """Progress event store backend + credentials.

    ``dsn`` is required for the postgres backend and ignored for sqlite.
    For sqlite, the path comes from ``MONET_PROGRESS_DB``.

    ``custom_backend`` overrides the built-in backend enum entirely.  When
    set to a ``module.path:factory`` string, the factory is called with no
    arguments and the result is used as both writer and reader.  The
    ``MONET_PROGRESS_BACKEND`` enum is ignored in that case.
    """

    model_config = ConfigDict(frozen=True)

    backend: ProgressBackend | None = None
    dsn: str | None = None
    custom_backend: str | None = None

    @classmethod
    def load(cls) -> ProgressConfig | None:
        """Return config if ``MONET_PROGRESS_BACKEND`` or
        ``MONET_PROGRESS_CUSTOM_BACKEND`` is set, else ``None``."""
        custom = read_str(MONET_PROGRESS_CUSTOM_BACKEND)
        raw = read_enum(MONET_PROGRESS_BACKEND, _PROGRESS_BACKENDS)
        if raw is None and custom is None:
            return None
        planes = read_toml_section("planes")
        progress_section = planes.get("progress", {})
        backend = ProgressBackend(raw) if raw is not None else None
        dsn = (
            read_str(MONET_PROGRESS_DSN)
            if backend == ProgressBackend.POSTGRES
            else None
        )
        if backend == ProgressBackend.POSTGRES and dsn is None:
            dsn = progress_section.get("dsn")
        return cls(backend=backend, dsn=dsn, custom_backend=custom)

    def validate_for_boot(self) -> None:
        if self.custom_backend is not None:
            from .._resolve import validate_dotted_path

            validate_dotted_path(self.custom_backend, MONET_PROGRESS_CUSTOM_BACKEND)
            return
        if self.backend == ProgressBackend.POSTGRES and not self.dsn:
            raise ConfigError(
                "planes.progress.dsn",
                None,
                "a Postgres DSN (required when progress backend is postgres)",
            )


class PlanesConfig(BaseModel):
    """Split-plane deployment configuration.

    Loaded from the optional ``[planes]`` section in ``monet.toml``.
    All fields have defaults so the section can be absent entirely for
    S1/S2/S3 unified deployments.
    """

    model_config = ConfigDict(frozen=True)

    data_url: str | None = None
    progress: ProgressConfig | None = None

    @classmethod
    def load(cls) -> PlanesConfig:
        planes = read_toml_section("planes")
        data_url = read_str(MONET_DATA_PLANE_URL) or (planes.get("data_url") or None)
        progress = ProgressConfig.load()
        return cls(data_url=data_url, progress=progress)

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "data_url": self.data_url,
            "progress_backend": (self.progress.backend if self.progress else _UNSET),
        }
