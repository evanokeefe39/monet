from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AdapterError(Exception):
    """Wire-format error with machine-readable code."""

    message: str
    code: str = field(default="AGENT_ERROR")

    def to_dict(self) -> dict[str, str]:
        return {"error": self.message, "error_code": self.code}
