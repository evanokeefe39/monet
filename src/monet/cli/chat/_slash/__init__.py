"""Slash-command subsystem — router, ghost-text suggester, and overlay widget."""

from monet.cli.chat._slash._overlay import SlashOverlay
from monet.cli.chat._slash._router import AppActions, CommandContext, dispatch_slash
from monet.cli.chat._slash._suggester import RegistrySuggester, SlashCommandProvider

__all__ = [
    "AppActions",
    "CommandContext",
    "RegistrySuggester",
    "SlashCommandProvider",
    "SlashOverlay",
    "dispatch_slash",
]
