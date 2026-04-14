"""Split-fleet demo agents.

Importing this package registers ``fast_agent`` (pool="fast") and
``heavy_agent`` (pool="heavy"). The monet worker scans ``--path agents``
and imports each module it discovers.
"""

from . import fast_agent, heavy_agent  # noqa: F401
