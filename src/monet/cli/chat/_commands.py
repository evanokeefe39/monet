"""Re-export wrapper — content moved to _slash/_router.py."""

from monet.cli.chat._slash._router import (
    AppActions,
    CommandContext,
    _cmd_switch,
    dispatch_slash,
)

__all__ = [
    "AppActions",
    "CommandContext",
    "_cmd_switch",
    "dispatch_slash",
]
