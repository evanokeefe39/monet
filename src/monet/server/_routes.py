"""REST API routes for the monet orchestration server.

This module is now a thin wrapper around the modularized routes in the
``monet.server.routes`` package.
"""

from monet.server.routes import router
from monet.server.routes._artifacts import (
    _mermaid_escape,
    _render_artifact_html,
    _render_work_brief_dag,
    _render_work_brief_html,
)
from monet.server.routes._common import _DAG_TASK_CHAR_BUDGET

__all__ = ["router"]

# Re-export private helpers for legacy tests
__all__ += [
    "_DAG_TASK_CHAR_BUDGET",
    "_mermaid_escape",
    "_render_artifact_html",
    "_render_work_brief_dag",
    "_render_work_brief_html",
]
