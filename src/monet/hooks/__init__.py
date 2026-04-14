"""Built-in worker-side hooks.

Importing this package registers all hooks at import time. Workers
should import ``monet.hooks`` during startup so all hooks are active
before the claim loop begins.
"""

from __future__ import annotations

from . import plan_context  # noqa: F401 — imports for side-effects

__all__: list[str] = []
