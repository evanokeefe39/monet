"""Interactive HITL prompt helpers.

Lifted out of the previous monolithic ``cli.py`` so the workflow phase
functions can stay testable. Both prompt helpers block on ``input()`` —
mock or monkeypatch them in tests.
"""

from __future__ import annotations

from typing import Any


def prompt_planning_decision() -> dict[str, Any]:
    """Approve, reject, or send feedback for a draft work brief.

    Returns the dict that ``planning_graph.human_approval`` consumes via
    ``Command(resume=...)``:

      ``{"approved": True}`` — accept the brief
      ``{"approved": False, "feedback": None}`` — hard reject, exit
      ``{"approved": False, "feedback": "..."}`` — request a revision
    """
    while True:
        print('\n  [a]pprove / [r]eject / [f]eedback "your notes"')
        raw = input("  > ").strip()
        if not raw:
            continue
        lower = raw.lower()
        if lower.startswith("a"):
            return {"approved": True}
        if lower.startswith("r"):
            return {"approved": False, "feedback": None}
        if lower.startswith("f"):
            feedback = raw[1:].strip().strip('"').strip("'").strip()
            if not feedback:
                feedback = raw[len("feedback") :].strip().strip('"').strip("'").strip()
            if not feedback:
                print("  Please provide feedback text.")
                continue
            return {"approved": False, "feedback": feedback}
        print("  Invalid input. Try again.")


def prompt_execution_decision() -> dict[str, Any]:
    """Continue, abort, or pass feedback at an execution HITL gate.

    Returns the dict that ``execution_graph.human_interrupt`` consumes:

      ``{"action": "continue"}``
      ``{"action": "abort", "feedback": "..."}``
      ``{"action": "continue", "feedback": "..."}``
    """
    while True:
        print('\n  [c]ontinue / [a]bort / [f]eedback "your notes"')
        raw = input("  > ").strip()
        if not raw:
            continue
        lower = raw.lower()
        if lower.startswith("c"):
            return {"action": "continue"}
        if lower.startswith("a"):
            return {"action": "abort", "feedback": "Aborted by user"}
        if lower.startswith("f"):
            feedback = raw[1:].strip().strip('"').strip("'").strip()
            if not feedback:
                feedback = raw[len("feedback") :].strip().strip('"').strip("'").strip()
            return {"action": "continue", "feedback": feedback}
        print("  Invalid input. Try again.")
