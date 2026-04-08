"""Reference monet client — drives the SDK reference stack via a
LangGraph Server. See ``cli.py`` for the entry point.

Importing this package also works from the repo root via
``python -m examples.social_media_llm`` — the package ``__init__.py``
and ``__main__.py`` wire the flat modules (``cli``, ``app``, etc.)
into this namespace so either invocation style works.
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
