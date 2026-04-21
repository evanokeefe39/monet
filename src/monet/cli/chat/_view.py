"""Transcript styling helpers for the monet chat TUI."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.text import Text

from monet.cli.chat._themes import MONET_DARK as _T

if TYPE_CHECKING:
    from monet.client._events import AgentProgress

_URL_RE = re.compile(r"https?://\S+")
_AGENT_TAG_RE = re.compile(r"^\[[\w-]+:[\w-]+\]")
_V = _T.variables

#: Default per-role styles for transcript tag highlighting.
DEFAULT_TAG_STYLES: dict[str, str] = {
    "[user]": f"italic {_T.warning}",
    "[assistant]": f"italic {_V['tag-assistant']}",
    "[info]": f"italic {_T.accent}",
    "[error]": f"italic {_V['tag-error']}",
    "│": f"dim italic {_V['progress-rule']}",
    "error: ": f"italic {_V['tag-error']}",
    "[hint]": f"dim italic {_V['tag-hint']}",
}

#: Tags where the style should extend to the entire rest of the line.
_FULL_LINE_TAGS: frozenset[str] = frozenset({"│", "error: ", "[hint]"})

#: Mapping from ``/colors <role>`` argument to the matching transcript tag.
ROLE_TAGS: dict[str, str] = {
    "user": "[user]",
    "assistant": "[assistant]",
    "info": "[info]",
    "progress": "│",
    "error": "[error]",
}


def _linkify(text: Text, content: str, offset: int = 0) -> None:
    """Add clickable link spans for every URL found in *content*."""
    if "://" not in content:
        return
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
            text = Text(overflow="fold", no_wrap=False)
            text.append(tag, style=style)
            if tag in _FULL_LINE_TAGS:
                text.append(rest, style=style)
            else:
                text.append(rest)
            _linkify(text, rest, offset=len(tag))
            return text
    m = _AGENT_TAG_RE.match(line)
    if m:
        tag = m.group()
        rest = line[len(tag) :]
        text = Text(overflow="fold", no_wrap=False)
        text.append(tag, style=_T.primary)
        text.append(rest)
        _linkify(text, rest, offset=len(tag))
        return text
    text = Text(line, overflow="fold", no_wrap=False)
    _linkify(text, line)
    return text


def format_agent_header(agent_key: str) -> str:
    """Return the header line for the first event of an agent."""
    return f"[{agent_key}]"


def format_progress_line(progress: AgentProgress) -> str | None:
    """Return a transcript line, or None to suppress (agent:started/completed)."""
    status = progress.status or "..."
    if status in {"agent:started", "agent:completed"}:
        return None
    if status in {"agent:failed", "agent:error"}:
        reason = progress.reasons or status
        return f"error: {reason}"
    return f"│ {status}"
