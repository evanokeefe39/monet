"""Deterministic stub LLM — canned responses keyed by task prefix.

Keeps the example hermetic so the e2e test does not need network or
provider credentials. Swap for a real provider (OpenAI, Anthropic,
Gemini) in any real deployment by replacing this module.
"""

from __future__ import annotations

import hashlib


def canned_response(task: str, *, kind: str) -> str:
    """Return a deterministic canned string for *kind* + *task*.

    The hash is only there to make each response distinct per task so
    tests can assert on content continuity across turns.
    """
    digest = hashlib.sha1(task.encode("utf-8")).hexdigest()[:8]
    templates = {
        "plan": (
            f"Plan for '{task[:60]}':\n"
            "  1. research_context\n"
            "  2. draft_output\n"
            f"(plan digest {digest})"
        ),
        "research": (
            f"Research findings for '{task[:60]}': three key points (r-{digest})."
        ),
        "write": (
            f"Draft: here is the written output about '{task[:60]}'. (w-{digest})"
        ),
        "respond": (f"[conversationalist] Replying to: {task[:120]} (c-{digest})"),
    }
    return templates.get(kind, f"[{kind}] {task[:80]} ({digest})")
