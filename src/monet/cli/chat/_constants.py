"""Magic values for the monet chat TUI.

Everything that used to be a module-level constant at the top of
``_chat_app.py`` lives here: slash commands, welcome splash content,
timeouts, pulse durations and peak colours, env-var lookups. Grouped so
a reviewer can eyeball the UX knobs without wading through the app
class.
"""

from __future__ import annotations

import os

from monet.config import MONET_CHAT_BORDER_COLOR, MONET_CHAT_PULSE

#: Slash commands handled by the TUI itself (not forwarded to the server).
#: Pairs of ``(command, short description)`` so the slash-completion
#: dropdown can render a dim hint next to each entry.
TUI_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/new", "start a fresh thread"),
    ("/clear", "alias for /new"),
    ("/threads", "open the threads sidebar"),
    ("/switch", "resume an existing thread by id"),
    ("/agents", "open the agents sidebar"),
    ("/artifacts", "open the artifacts sidebar"),
    ("/runs", "list recent pipeline runs"),
    ("/colors", "show or change the palette"),
    ("/help", "list TUI commands"),
    ("/quit", "leave the REPL"),
    ("/exit", "leave the REPL"),
)

#: Fallback text for the toolbar indicator before first refresh / when
#: the registry is unreachable. Real content is ``N agents · M artifacts``,
#: refreshed on mount + after every turn completes.
DEFAULT_TOOLBAR_HINTS = "… agents · … artifacts"

#: ASCII logo placeholder for the empty-state welcome screen.
WELCOME_LOGO: tuple[str, ...] = (
    "   __  __                 _   ",
    "  |  \\/  | ___  _ __   ___| |_ ",
    "  | |\\/| |/ _ \\| '_ \\ / _ \\ __|",
    "  | |  | | (_) | | | |  __/ |_ ",
    "  |_|  |_|\\___/|_| |_|\\___|\\__|",
)

#: Key commands shown beneath the logo on the empty-state welcome screen.
WELCOME_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/threads", "switch chat threads"),
    ("/agents", "browse agent commands"),
    ("/new", "start a fresh thread"),
    ("ctrl+p", "command menu"),
    ("ctrl+c (x2)", "quit"),
)

#: How often the toolbar indicator repolls the registry + artifact store.
INDICATOR_REFRESH_SECONDS = 5.0

#: Seconds the toolbar holds the confirm-exit hint before disarming ctrl+c.
EXIT_CONFIRM_TIMEOUT = 5.0

#: Border pulse is enabled unless the operator opts out via env var.
PULSE_ENABLED = os.environ.get(MONET_CHAT_PULSE, "1").lower() not in {
    "0",
    "off",
    "false",
    "no",
}

#: Operator-supplied border colour for tmux / multi-pane differentiation.
#: Accepts any Textual-parseable color string (hex, named, rgb(...)). When
#: set, it becomes the pulse PEAK colour for both busy and idle pulses, so
#: every pane carries a distinct "signature" hue. Unset → theme ``$accent``.
CUSTOM_BORDER_COLOR = os.environ.get(MONET_CHAT_BORDER_COLOR, "").strip()

#: Seconds per half-cycle for the border breath. Same rhythm across both
#: widgets so the transition from idle-prompt to busy-transcript feels
#: like a single pulse passing between the two rather than two different
#: speeds.
BUSY_PULSE_DURATION = 1.0
IDLE_PULSE_DURATION = 1.0

#: Theme-variable names used as the pulse peak when no
#: ``MONET_CHAT_BORDER_COLOR`` override is set.
BUSY_PULSE_PEAK = "accent"
IDLE_PULSE_PEAK = "accent"

#: Theme-variable name for the resting / inactive border colour. Deliberately
#: dim so the pulse reads as a clear contrast swing against this base.
IDLE_BORDER_VAR = "panel-lighten-2"
