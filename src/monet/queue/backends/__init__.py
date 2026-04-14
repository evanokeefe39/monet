"""Backend implementations for the task queue.

Each backend module is imported lazily by ``monet.queue.__getattr__``.
Add backends by creating a new module here and wiring it up in the
parent package's lazy loader.
"""

from __future__ import annotations

__all__: list[str] = []
