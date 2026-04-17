"""Transcript styling helpers for the monet chat TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

if TYPE_CHECKING:
    from monet.client._events import AgentProgress

#: Default per-role styles for transcript tag highlighting.
DEFAULT_TAG_STYLES: dict[str, str] = {
    "[user]": "bold #3b82f6",
    "[assistant]": "bold #a855f7",
    "[info]": "bold #9ca3af",
    "[progress]": "bold #ca8a04",
    "[error]": "bold red",
}

#: Mapping from ``/colors <role>`` argument to the matching transcript tag.
ROLE_TAGS: dict[str, str] = {
    "user": "[user]",
    "assistant": "[assistant]",
    "info": "[info]",
    "progress": "[progress]",
    "error": "[error]",
}


def styled_line(line: str, tag_styles: dict[str, str]) -> Text:
    """Return a ``rich.Text`` with the leading tag coloured per *tag_styles*."""
    for tag, style in tag_styles.items():
        if line.startswith(tag):
            rest = line[len(tag) :]
            text = Text()
            text.append(tag, style=style)
            text.append(rest)
            return text
    return Text(line)


def format_progress_line(progress: AgentProgress) -> str:
    """Render an :class:`AgentProgress` as a ``[progress]`` transcript line."""
    status = progress.status or "..."
    return f"[progress] {progress.agent_id}: {status}"
