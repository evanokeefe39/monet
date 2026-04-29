from __future__ import annotations

from pathlib import Path  # noqa: TC003 — pydantic needs this at runtime
from typing import Any

from pydantic import BaseModel, ConfigDict

from .._env import (
    MONET_ARTIFACT_BACKEND,
    MONET_ARTIFACTS_DIR,
    MONET_DISTRIBUTED,
    read_bool,
    read_path,
    read_str,
)
from ._common import _UNSET


class ArtifactsConfig(BaseModel):
    """Artifact store root + distributed-mode flag."""

    model_config = ConfigDict(frozen=True)

    root: Path | None = None
    distributed: bool = False
    backend: str | None = None

    @classmethod
    def load(cls) -> ArtifactsConfig:
        return cls(
            root=read_path(MONET_ARTIFACTS_DIR),
            distributed=read_bool(MONET_DISTRIBUTED, default=False),
            backend=read_str(MONET_ARTIFACT_BACKEND),
        )

    def resolve_root(self, default: Path) -> Path:
        """Return the effective artifact root, falling back to *default*."""
        return self.root if self.root is not None else default

    def validate_for_boot(self) -> None:
        """Fail fast if the backend dotted path is malformed or unresolvable."""
        if self.backend is None:
            return
        from .._resolve import validate_dotted_path

        validate_dotted_path(self.backend, MONET_ARTIFACT_BACKEND)

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "root": str(self.root) if self.root else _UNSET,
            "distributed": self.distributed,
            "backend": self.backend or _UNSET,
        }
