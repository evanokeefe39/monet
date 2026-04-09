"""Structured output parsing with explicit fallback policies.

Every graph node that consumes AgentResult.output as JSON does the same
thing: check type, try json.loads, handle JSONDecodeError. The fallback
policy is the part that varies and the part that matters — and it must
be visible at the call site, not buried in an except block.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from monet.types import AgentResult


class ParseFailure:
    """Returned when output is not valid JSON. Carries the raw text."""

    __slots__ = ("raw",)

    def __init__(self, raw: str) -> None:
        self.raw = raw


def parse_json_output(result: AgentResult) -> dict[str, Any] | ParseFailure:
    """Extract JSON dict from AgentResult.output.

    Returns the parsed dict or ParseFailure. Never silently falls back —
    the caller decides what ParseFailure means for their routing.
    """
    if isinstance(result.output, dict):
        return result.output
    if isinstance(result.output, str) and result.output.strip():
        try:
            parsed = json.loads(result.output)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return ParseFailure(result.output[:200])
    return ParseFailure("")
