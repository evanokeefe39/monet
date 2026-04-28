"""Transcript styling helpers for the monet chat TUI."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.text import Text

from monet.cli.chat._colors import ACCENT as _ACCENT
from monet.cli.chat._colors import ERROR as _ERROR
from monet.cli.chat._colors import MUTED as _MUTED
from monet.cli.chat._colors import PRIMARY as _PRIMARY
from monet.cli.chat._colors import SECONDARY as _SECONDARY

if TYPE_CHECKING:
    from monet.client._events import AgentProgress

_URL_RE = re.compile(r"https?://\S+")
_AGENT_TAG_RE = re.compile(r"^\[[\w-]+:[\w-]+\]")

TAG_STYLES: dict[str, str] = {
    "[user]": f"italic {_PRIMARY}",
    "[assistant]": f"italic {_PRIMARY}",
    "[info]": f"italic {_ACCENT}",
    "[error]": f"italic {_ERROR}",
    "│": f"dim italic {_MUTED}",
    "error: ": f"italic {_ERROR}",
    "[hint]": f"dim italic {_SECONDARY}",
}

_FULL_LINE_TAGS: frozenset[str] = frozenset({"│", "error: ", "[hint]"})


def _linkify(text: Text, content: str, offset: int = 0) -> None:
    """Add clickable link spans for every URL found in *content*."""
    if "://" not in content:
        return
    for m in _URL_RE.finditer(content):
        text.stylize(f"link {m.group()}", offset + m.start(), offset + m.end())


def styled_line(line: str) -> Text:
    """Return a ``rich.Text`` with the leading tag coloured per theme.

    URLs anywhere in the line are made clickable via Rich's link style so
    terminals that support OSC 8 (most modern ones) render them as hyperlinks.
    """
    for tag, style in TAG_STYLES.items():
        if line.startswith(tag):
            rest = line[len(tag) :]
            text = Text(overflow="fold", no_wrap=False)
            text.append(tag, style=style)
            if tag in _FULL_LINE_TAGS:
                text.append(rest, style=style)
            else:
                text.append(rest, style=_MUTED)
            _linkify(text, rest, offset=len(tag))
            return text
    m = _AGENT_TAG_RE.match(line)
    if m:
        tag = m.group()
        rest = line[len(tag) :]
        text = Text(overflow="fold", no_wrap=False)
        text.append(tag, style=_PRIMARY)
        text.append(rest, style=_MUTED)
        _linkify(text, rest, offset=len(tag))
        return text
    text = Text(line, style=_MUTED, overflow="fold", no_wrap=False)
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
