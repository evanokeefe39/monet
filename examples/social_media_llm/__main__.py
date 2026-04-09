"""Fallback for ``python -m`` invocation.

The canonical way to run this example is ``uv run python cli.py``.
This module exists so ``python -m examples.social_media_llm`` also
works when invoked from the repo root.
"""

from __future__ import annotations

import sys
from pathlib import Path

_here = str(Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

from cli import main  # noqa: E402

main()
