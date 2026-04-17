"""Recruitment capability agents — importing this module registers them.

Two agents + two stub modules live here:

- ``code_executor`` — subprocess-sandboxed code evaluator. Commands:
  ``run`` (single candidate) and ``eval_all`` (list of candidates).
- ``data_analyst`` — queries the artifact index for ``run_summary``
  records and scores the roster. Commands: ``query`` and ``score_agents``.

Both agents have docstrings tuned for the planner's manifest-driven
prompt (see ``src/monet/agents/planner/templates/plan.j2``).
"""

from __future__ import annotations

# Side-effect imports: @agent decorators fire on import.
from . import code_executor as code_executor
from . import data_analyst as data_analyst
