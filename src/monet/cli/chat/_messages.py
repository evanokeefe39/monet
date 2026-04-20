"""Custom Textual Message types for inter-widget communication."""

from __future__ import annotations

from typing import Any

from textual.message import Message


class PromptSubmitted(Message):
    """User pressed Enter in the prompt."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class WelcomeDismissed(Message):
    """Any key pressed while welcome is visible."""


class TurnStarted(Message):
    """A turn worker has begun streaming."""


class TurnFinished(Message):
    """A turn worker completed."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self.error = error


class TranscriptAppend(Message):
    """Request to append a line to the transcript."""

    def __init__(self, line: str, *, markdown: bool = False) -> None:
        super().__init__()
        self.line = line
        self.markdown = markdown


class HitlMountRequest(Message):
    """Turn loop requests mounting an inline HITL widget."""

    def __init__(self, envelope: Any) -> None:
        super().__init__()
        self.envelope = envelope


class HitlSubmitted(Message):
    """HITL widget submitted a payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = payload


class HitlDismissed(Message):
    """User cancelled the HITL form."""
