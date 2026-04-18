"""Custom Textual themes for the monet chat TUI.

We ship two themes — one dark (default), one light — tuned to the
transcript tag palette in :mod:`~monet.cli.chat._view`. The themes
replace Textual's built-in dark/light defaults so the chat has a
coherent, monet-native look rather than the Textual system palette
peeking through.

Registered on app mount via ``self.register_theme(...)``; the active
theme is selected by ``self.theme = "monet-dark"`` (or whatever
``UserChatStyle.theme`` specifies).
"""

from __future__ import annotations

from textual.theme import Theme

#: Default dark theme. Magenta/purple accent matches the historic
#: ``#9b59b6`` the border pulse falls back to when no override is set,
#: and the pink ``$primary`` tracks the ``[info]`` transcript tag so
#: borders, status pills, and tag colours read as one palette.
MONET_DARK = Theme(
    name="monet-dark",
    primary="#e74c8b",  # bright pink — borders, headers, focus rings
    secondary="#3498db",  # blue — [user] tag
    accent="#9b59b6",  # purple — pulse peak, highlights
    warning="#f39c12",  # orange — [progress] tag
    error="#e74c3c",  # red — destructive confirmations
    success="#27ae60",  # green — [error] tag uses this too
    foreground="#e0e0e8",
    background="#000000",
    surface="#0a0a12",
    panel="#14141e",
    boost="#1e1e2a",
    dark=True,
    variables={
        "text-muted": "#7a7a85",
        "panel-lighten-1": "#1e1e2a",
        "panel-lighten-2": "#2a2a38",
    },
)

#: Light companion for operators on bright terminals. Same hue family,
#: inverted surfaces. Kept intentionally simple — monet's visual
#: identity is dark-first; this is a fallback, not a second product.
MONET_LIGHT = Theme(
    name="monet-light",
    primary="#c0357a",
    secondary="#1f6fb3",
    accent="#7a3f91",
    warning="#c47713",
    error="#b33a2d",
    success="#1e8a4a",
    foreground="#1a1a22",
    background="#f5f5f7",
    surface="#eaeaef",
    panel="#dedee5",
    boost="#d2d2da",
    dark=False,
    variables={
        "text-muted": "#6a6a78",
        "panel-lighten-1": "#d2d2da",
        "panel-lighten-2": "#c4c4cd",
    },
)

#: Every theme the chat app registers. The first entry becomes the
#: default when no operator override is configured.
MONET_THEMES: tuple[Theme, ...] = (MONET_DARK, MONET_LIGHT)
