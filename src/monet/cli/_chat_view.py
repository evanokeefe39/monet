"""Transcript styling helpers for the monet chat TUI."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.text import Text

if TYPE_CHECKING:
    from monet.client._events import AgentProgress

_URL_RE = re.compile(r"https?://\S+")

#: Default per-role styles for transcript tag highlighting.
DEFAULT_TAG_STYLES: dict[str, str] = {
    "[user]": "bold #3498db",
    "[assistant]": "bold #9b59b6",
    "[info]": "bold #e74c8b",
    "[progress]": "bold #f39c12",
    "[error]": "bold #27ae60",
    # Ephemeral UX hints (welcome banner, transient guidance). Dim so
    # users recognise them as advisory, not conversational.
    "[hint]": "dim italic",
}

#: Mapping from ``/colors <role>`` argument to the matching transcript tag.
ROLE_TAGS: dict[str, str] = {
    "user": "[user]",
    "assistant": "[assistant]",
    "info": "[info]",
    "progress": "[progress]",
    "error": "[error]",
}


def _linkify(text: Text, content: str, offset: int = 0) -> None:
    """Add clickable link spans for every URL found in *content*."""
    for m in _URL_RE.finditer(content):
        text.stylize(f"link {m.group()}", offset + m.start(), offset + m.end())


def styled_line(line: str, tag_styles: dict[str, str]) -> Text:
    """Return a ``rich.Text`` with the leading tag coloured per *tag_styles*.

    URLs anywhere in the line are made clickable via Rich's link style so
    terminals that support OSC 8 (most modern ones) render them as hyperlinks.
    """
    for tag, style in tag_styles.items():
        if line.startswith(tag):
            rest = line[len(tag) :]
            text = Text()
            text.append(tag, style=style)
            text.append(rest)
            _linkify(text, rest, offset=len(tag))
            return text
    text = Text(line)
    _linkify(text, line)
    return text


def format_progress_line(progress: AgentProgress) -> str:
    """Render an :class:`AgentProgress` as a ``[progress]`` transcript line."""
    status = progress.status or "..."
    return f"[progress] {progress.agent_id}: {status}"
