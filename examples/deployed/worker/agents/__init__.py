"""Agent registration for the deployed worker example.

Importing :mod:`monet.agents` triggers the ``@agent`` decorators in the
reference agent modules (planner, researcher, writer, qa, publisher),
which registers them with the worker's handler registry.

The monet worker scans this ``agents/`` directory via ``--path agents``
at startup; this file is the only entry point it needs.
"""

import monet.agents  # noqa: F401
