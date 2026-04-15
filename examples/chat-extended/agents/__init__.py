"""Register example agents when this package is imported.

``server_graphs.py`` imports ``agents`` at module level so the
``@agent`` decorators fire before Aegra compiles the graphs; every
declared capability shows up in the manifest (and therefore in
``monet chat``'s dynamic slash-command menu).
"""

from . import report_writer, search  # noqa: F401
