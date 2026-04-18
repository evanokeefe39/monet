"""Chat TUI package — interactive ``monet chat`` REPL backed by Textual.

Public surface:

- :func:`chat` — the Click command registered under ``monet chat``.
- :class:`ChatApp` — the Textual app (exposed for tests).

Internal submodules (``_cli``, ``_app``, ``_pulse``, ``_welcome``, ``_turn``,
``_hitl``, ``_pickers``, ``_slash``, ``_view``, ``_constants``) are private.
"""

from __future__ import annotations

from monet.cli.chat._app import ChatApp
from monet.cli.chat._cli import chat

__all__ = ["ChatApp", "chat"]
