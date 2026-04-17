"""Per-user chat TUI style profile.

Loaded from ``~/.monet/chat.toml`` at ``monet chat`` startup. Project-level
``monet.toml`` is intentionally not consulted here — style is a personal
preference, not a team setting.

Example ``~/.monet/chat.toml``::

    [style]
    user        = "bold #3498db"
    assistant   = "bold #9b59b6"
    info        = "bold #e74c8b"
    progress    = "bold #f39c12"
    error       = "bold #27ae60"
    border_color = "#9b59b6"
    pulse        = true
"""

from __future__ import annotations

import logging
import tomllib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import BaseModel, ConfigDict

from monet._ports import state_dir

_log = logging.getLogger("monet.config.user_chat")


class UserChatStyle(BaseModel):
    """Per-user style profile for the chat TUI.

    All fields optional — unset means "use the built-in default".
    """

    model_config = ConfigDict(extra="ignore")

    user: str | None = None
    assistant: str | None = None
    info: str | None = None
    progress: str | None = None
    error: str | None = None
    border_color: str | None = None
    pulse: bool | None = None

    def tag_styles(self, defaults: dict[str, str]) -> dict[str, str]:
        """Merge this profile's tag colours over *defaults*.

        Only keys with non-None values override; the rest keep the default.
        """
        merged = dict(defaults)
        mapping = {
            "[user]": self.user,
            "[assistant]": self.assistant,
            "[info]": self.info,
            "[progress]": self.progress,
            "[error]": self.error,
        }
        for tag, value in mapping.items():
            if value is not None:
                merged[tag] = value
        return merged


def user_chat_config_path() -> Path:
    """Return ``~/.monet/chat.toml`` (file may not exist yet)."""
    return state_dir() / "chat.toml"


def load_user_chat_style() -> UserChatStyle:
    """Read ``~/.monet/chat.toml`` and return a :class:`UserChatStyle`.

    Missing file or parse errors are logged and the empty (all-defaults)
    profile is returned so the TUI always starts.
    """
    path = user_chat_config_path()
    if not path.exists():
        return UserChatStyle()
    try:
        raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _log.warning("Failed to parse %s — using defaults", path, exc_info=True)
        return UserChatStyle()
    style_section = raw.get("style") or {}
    if not isinstance(style_section, dict):
        _log.warning("%s [style] is not a table — using defaults", path)
        return UserChatStyle()
    try:
        return UserChatStyle.model_validate(style_section)
    except Exception:
        _log.warning("Invalid [style] in %s — using defaults", path, exc_info=True)
        return UserChatStyle()
