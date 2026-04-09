"""Reference monet client — drives the SDK reference stack via a
LangGraph Server. See ``cli.py`` for the entry point.

Run from the example directory::

    cd examples/social_media_llm
    uv run python cli.py "AI in marketing"
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the flat modules (cli.py, app.py, client.py, ...) importable as
# top-level names when this package is imported via
# ``examples.social_media_llm``. The modules themselves use bare
# ``from app import ...`` style imports so that they also work when the
# example dir is the script root (``uv run python cli.py "topic"``).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import main

__all__ = ["main"]
