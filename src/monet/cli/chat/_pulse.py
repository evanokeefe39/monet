"""Breathing border-pulse animation for the monet chat TUI.

Textual does not animate border colours via ``styles.animate``
(``border_*_color`` is not in ``RenderStyles.ANIMATABLE``), so the
pulse is a manual timer: every tick interpolates between a dim base
and a bright peak using a sine wave and writes the result to all four
border edges of the target widget.

:class:`BorderPulseController` owns the timer state so ``ChatApp`` can
just call ``pulse.start("#prompt", peak_var="accent", duration=1.0)``
without reaching into the pulse internals.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from typing import TYPE_CHECKING, Any

from textual.color import Color

from monet.cli.chat._constants import IDLE_BORDER_VAR

if TYPE_CHECKING:
    from textual.app import App


class BorderPulseController:
    """Owns the per-widget pulse timers and the shared idle/peak colours.

    One controller per app. Pulse state is keyed by selector string so
    the same helper drives the prompt pulse (idle) and the transcript
    pulse (busy) without collision.
    """

    _TICK_INTERVAL = 0.05  # 20fps — smooth enough, cheap enough

    def __init__(self, app: App[Any], *, override_color: str = "") -> None:
        self._app = app
        self._timers: dict[str, Any] = {}
        self.override_color: str = override_color

    # ── lifecycle ─────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Cancel every live pulse timer. Safe to call repeatedly."""
        for selector in list(self._timers):
            timer = self._timers.pop(selector, None)
            if timer is not None:
                with contextlib.suppress(Exception):
                    timer.stop()

    # ── public pulse controls ─────────────────────────────────────

    def start(self, selector: str, *, peak_var: str, duration: float) -> None:
        """Begin a breathing border pulse on *selector*.

        ``peak_var`` is a bare theme-variable name (e.g. ``"accent"``).
        ``MONET_CHAT_BORDER_COLOR`` overrides the peak when set so tmux /
        multi-pane operators can give each chat instance a signature hue.
        """
        if selector in self._timers:
            return  # already pulsing
        widget = self._widget(selector)
        if widget is None:
            return
        base = self.idle_color()
        peak = self.peak_color(peak_var)
        start = asyncio.get_event_loop().time()

        def _tick() -> None:
            with contextlib.suppress(Exception):
                self._tick(widget, base, peak, duration, start)

        self._timers[selector] = self._app.set_interval(self._TICK_INTERVAL, _tick)

    def stop(self, selector: str) -> None:
        """Halt a running pulse and snap the border back to the dim idle colour."""
        timer = self._timers.pop(selector, None)
        if timer is not None:
            with contextlib.suppress(Exception):
                timer.stop()
        widget = self._widget(selector)
        if widget is None:
            return
        self._paint_border(widget, self.idle_color())

    def apply_idle_borders(self, selectors: tuple[str, ...]) -> None:
        """Paint every widget in *selectors* with the dim idle colour.

        Called on mount so the chat starts with visibly quiet borders —
        the pulse peak then reads as an obvious contrast swing.
        """
        base = self.idle_color()
        for selector in selectors:
            widget = self._widget(selector)
            if widget is not None:
                self._paint_border(widget, base)

    def active_selectors(self) -> list[str]:
        """Snapshot of which selectors have a live pulse timer."""
        return list(self._timers)

    # ── colour helpers ────────────────────────────────────────────

    def idle_color(self) -> Color:
        """Dim colour used for inactive borders and as the pulse trough."""
        return self._resolve_css_color(IDLE_BORDER_VAR, "#1a1a2e")

    def peak_color(self, peak_var: str) -> Color:
        """Bright colour at the crest of the pulse.

        ``override_color`` wins when set — that's the point of the env var
        and the ``/colors border`` command.
        """
        if self.override_color:
            with contextlib.suppress(Exception):
                return Color.parse(self.override_color)
        return self._resolve_css_color(peak_var, "#9b59b6")

    def _resolve_css_color(self, var_name: str, fallback: str) -> Color:
        """Resolve a theme variable (e.g. ``accent``) to a concrete Color.

        ``var_name`` is the bare variable name without the leading ``$``.
        Falls back to ``fallback`` (a hex string) if the variable is
        not present — keeps the pulse from crashing under unusual themes.
        """
        variables = self._app.get_css_variables()
        raw = variables.get(var_name) or fallback
        try:
            return Color.parse(raw)
        except Exception:
            return Color.parse(fallback)

    # ── internals ─────────────────────────────────────────────────

    def _widget(self, selector: str) -> Any:
        with contextlib.suppress(Exception):
            return self._app.query_one(selector)
        return None

    def _tick(
        self,
        widget: Any,
        base: Color,
        peak: Color,
        duration: float,
        start: float,
    ) -> None:
        """One frame of the pulse loop — interpolate and repaint the border."""
        elapsed = asyncio.get_event_loop().time() - start
        # Sine wave from 0→1→0 with period = 2 * duration.
        phase = 0.5 - 0.5 * math.cos(math.pi * elapsed / duration)
        color = base.blend(peak, phase)
        self._paint_border(widget, color)

    @staticmethod
    def _paint_border(widget: Any, color: Color) -> None:
        current_top = widget.styles.border_top
        border_type = current_top[0] if current_top else "round"
        for edge in ("top", "right", "bottom", "left"):
            setattr(widget.styles, f"border_{edge}", (border_type, color))
