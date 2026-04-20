"""Constants for the monet chat TUI."""

from __future__ import annotations

#: Slash commands handled by the TUI itself (not forwarded to the server).
TUI_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/new", "start a fresh thread"),
    ("/clear", "alias for /new"),
    ("/threads", "open threads"),
    ("/switch", "resume an existing thread by id"),
    ("/agents", "browse agent commands"),
    ("/artifacts", "open artifacts"),
    ("/runs", "list recent pipeline runs"),
    ("/rename", "rename current thread"),
    ("/copy", "copy transcript to clipboard"),
    ("/colors", "show or change the palette"),
    ("/help", "list TUI commands"),
    ("/quit", "leave the REPL"),
    ("/exit", "leave the REPL"),
)

#: ASCII logo for the welcome screen.
WELCOME_LOGO: tuple[str, ...] = (
    "   __  __                 _   ",
    "  |  \\/  | ___  _ __   ___| |_ ",
    "  | |\\/| |/ _ \\| '_ \\ / _ \\ __|",
    "  | |  | | (_) | | | |  __/ |_ ",
    "  |_|  |_|\\___/|_| |_|\\___|\\__|",
)

#: Commands shown on the welcome screen.
WELCOME_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/threads", "manage conversation threads"),
    ("/agents", "manage agent pools"),
    ("/runs", "view run history"),
    ("/help", "show all commands"),
    ("/clear", "clear chat history"),
    ("/quit", "quit"),
)

#: How often the status bar refreshes counts.
INDICATOR_REFRESH_SECONDS = 5.0

#: Seconds the confirm-exit hint stays active.
EXIT_CONFIRM_TIMEOUT = 5.0
