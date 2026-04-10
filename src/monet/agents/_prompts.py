"""Jinja2 prompt-template loader for reference agents.

Each agent package owns a `templates/` subfolder. Call `make_env(Path(__file__).parent)`
from the agent module to get a Jinja Environment scoped to that agent's templates.

Autoescape is disabled — these templates render plain-text prompts for an LLM,
not HTML. `trim_blocks` + `lstrip_blocks` keep template logic from leaking
whitespace into the rendered prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jinja2 import (  # type: ignore[import-not-found]
    Environment,
    FileSystemLoader,
    StrictUndefined,
)
from langchain_core.output_parsers import StrOutputParser

if TYPE_CHECKING:
    from pathlib import Path

_str_parser = StrOutputParser()


def make_env(agent_dir: Path) -> Environment:
    """Build a Jinja2 Environment rooted at ``<agent_dir>/templates``."""
    return Environment(
        loader=FileSystemLoader(str(agent_dir / "templates")),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )


def extract_text(response: Any) -> str:
    """Pull plain text out of a langchain chat response.

    Thin wrapper around ``langchain_core.output_parsers.StrOutputParser``,
    which is the canonical helper for collapsing an ``AIMessage`` (whose
    ``content`` may be a bare string or a list of content blocks like
    ``[{"type": "text", "text": "..."}]`` for Gemini/Anthropic) into a
    single string. Use this rather than ``str(response.content)``, which
    produces Python repr for the list-of-dicts shape.
    """
    return str(_str_parser.invoke(response))
