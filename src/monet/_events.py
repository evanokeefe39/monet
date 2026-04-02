"""Event vocabulary handler for non-Python agent wrappers.

CLI agents and subprocesses communicate via JSON events on stdout.
handle_agent_event() routes each event type to the appropriate SDK function.

Event types:
    progress — forwarded to emit_progress()
    artifact — written to catalogue via write_artifact()
    result   — terminal event, output returned as string
    error    — translated to emit_signal() or typed exception
    log      — forwarded to get_run_logger()
"""

from __future__ import annotations

from typing import Any

from ._context import get_run_logger
from ._stubs import emit_progress, emit_signal, write_artifact
from ._types import Signal, SignalType
from .exceptions import EscalationRequired, NeedsHumanReview, SemanticError


async def handle_agent_event(event: dict[str, Any]) -> str | None:
    """Route a monet agent event to the appropriate SDK function.

    Returns the result string on 'result' event, None otherwise.
    Raises typed exceptions on 'error' events.
    """
    event_type = event.get("type")

    if event_type == "progress":
        data = {k: v for k, v in event.items() if k != "type"}
        emit_progress(data)

    elif event_type == "artifact":
        content = event.get("content", "")
        content_bytes = content.encode() if isinstance(content, str) else content
        await write_artifact(
            content=content_bytes,
            content_type=event.get("content_type", "text/plain"),
            summary=event.get("summary", ""),
            confidence=event.get("confidence", 0.8),
            completeness=event.get("completeness", "complete"),
            sensitivity_label=event.get("sensitivity_label", "internal"),
        )

    elif event_type == "result":
        output: str = event.get("output", "")
        return output

    elif event_type == "error":
        error_type = event.get("error_type", "semantic_error")
        message = event.get("message", "Agent error")
        if error_type == "needs_human_review":
            raise NeedsHumanReview(reason=message)
        if error_type == "escalation_required":
            raise EscalationRequired(reason=message)
        raise SemanticError(type=error_type, message=message)

    elif event_type == "log":
        level = event.get("level", "info")
        msg = event.get("message", "")
        logger = get_run_logger()
        log_fn = getattr(logger, level, logger.info)
        log_fn(msg)

    elif event_type == "signal":
        emit_signal(
            Signal(
                type=event.get("signal_type", SignalType.LOW_CONFIDENCE),
                reason=event.get("reason", ""),
                metadata=event.get("metadata"),
            )
        )

    return None
