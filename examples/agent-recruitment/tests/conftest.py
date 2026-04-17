"""Make the example's ``recruitment`` package importable without install.

Prepends ``examples/agent-recruitment/src`` to ``sys.path`` so the tests
can ``import recruitment...`` when run from the repo root via
``uv run pytest examples/agent-recruitment/tests``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))
