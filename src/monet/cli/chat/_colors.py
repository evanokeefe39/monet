"""Unified Rich Text color constants sourced from MONET_EMBER theme.

Import from here for Rich Text paths. Textual CSS paths use $primary/$accent etc.
"""

from __future__ import annotations

from monet.cli.chat._themes import MONET_EMBER

_V = MONET_EMBER.variables

PRIMARY: str = MONET_EMBER.primary or "#007065"
ACCENT: str = MONET_EMBER.accent or "#ae6002"
ERROR: str = MONET_EMBER.error or "#ae463d"
SECONDARY: str = MONET_EMBER.secondary or "#004c79"
MUTED: str = _V["text-muted"]
