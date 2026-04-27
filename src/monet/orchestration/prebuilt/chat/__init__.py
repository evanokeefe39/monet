"""Chat subpackage — slash commands, triage, respond, specialist, mount.

Public surface re-exports the graph builder, state schema, and triage
result class. Per-node modules are private (``_parse``, ``_triage``,
``_respond``, ``_specialist``, ``_format``, ``_lc``, ``_state``,
``_build``) and may be patched by tests via their qualified module path.
"""

from ._build import MAX_FOLLOWUP_ATTEMPTS, build_chat_graph
from ._state import ChatState
from ._triage import ChatTriageResult, TriageError

__all__ = [
    "MAX_FOLLOWUP_ATTEMPTS",
    "ChatState",
    "ChatTriageResult",
    "TriageError",
    "build_chat_graph",
]
