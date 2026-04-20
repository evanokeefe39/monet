"""Chat TUI package — interactive ``monet chat`` REPL backed by Textual.

Public surface:

- :func:`chat` — the Click command registered under ``monet chat``.
- :class:`ChatApp` — the Textual app (exposed for tests).
"""

from __future__ import annotations

from monet.cli.chat._app import ChatApp
from monet.cli.chat._cli import chat

__all__ = ["ChatApp", "chat"]
