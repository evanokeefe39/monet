"""Reference researcher agent — query synthesis with web search.

Two commands:
  - fast: quick search with fewer results (~30s)
  - deep: exhaustive search with provider selection at call time:
      1. EXA_API_KEY + exa_py importable  -> Exa semantic search + LLM synthesis
      2. TAVILY_API_KEY + langchain_community importable -> Tavily ReAct agent
      3. No fallback — raises EscalationRequired if no search succeeds

Both commands require a working search provider (Exa or Tavily). Research
based solely on LLM knowledge is never acceptable — the model's training
data is stale and unverifiable. If no search provider returns substantive
content, the agent raises ``EscalationRequired`` which produces a BLOCKING
signal and halts execution immediately.

Module-level imports stay LLM-only so ``import monet.agents`` succeeds
without exa_py or langchain_community installed.
"""

from __future__ import annotations

import asyncio
import functools
import os
from pathlib import Path
from typing import Any

from monet import agent, emit_progress, get_catalogue, get_run_logger
from monet.exceptions import EscalationRequired

from .._prompts import extract_text, make_env

_env = make_env(Path(__file__).parent)

# Real search+synthesis output is consistently 2-12KB. Under 500 chars is
# likely a tool error wrapped in an LLM apology. This is the researcher's
# own quality gate — the orchestrator can't know this threshold.
MIN_RESEARCH_CONTENT_LENGTH = 500


@functools.cache
def _get_model(model_string: str) -> Any:
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string)


_react_agent_cache: dict[str, Any] = {}


def _get_react_agent(model_string: str) -> Any:
    """Build-and-cache a ReAct agent wrapping the model with Tavily."""
    if model_string not in _react_agent_cache:
        from langchain_community.tools.tavily_search import (  # type: ignore[import-not-found]
            TavilySearchResults,
        )
        from langgraph.prebuilt import (
            create_react_agent,  # type: ignore[import-untyped]
        )

        model = _get_model(model_string)
        tavily = TavilySearchResults(max_results=10)
        _react_agent_cache[model_string] = create_react_agent(model, [tavily])
    return _react_agent_cache[model_string]


def _format_exa_results(results: Any) -> str:
    lines: list[str] = []
    for r in getattr(results, "results", []) or []:
        title = getattr(r, "title", "") or ""
        url = getattr(r, "url", "") or ""
        text = getattr(r, "text", "") or ""
        lines.append(f"## {title}")
        lines.append(f"Source: {url}")
        if text:
            lines.append(text[:2000])
        lines.append("")
    return "\n".join(lines)


def _last_ai_message(messages: list[Any]) -> str:
    from langchain_core.messages import AIMessage  # type: ignore[import-not-found]

    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            tool_calls = getattr(msg, "tool_calls", None)
            text = extract_text(msg)
            if text and not tool_calls:
                return text
    return ""


def _model_string() -> str:
    return os.environ.get("MONET_RESEARCHER_MODEL", "google_genai:gemini-2.5-flash")


async def _ainvoke_text(model_string: str, prompt: str) -> str:
    response = await _get_model(model_string).ainvoke(
        [{"role": "user", "content": prompt}]
    )
    return extract_text(response)


async def _search(
    task: str,
    context: list[dict[str, Any]],
    model_string: str,
    *,
    max_results: int = 10,
) -> str:
    """Run web search via Exa or Tavily and synthesise results.

    Tries Exa first (if ``EXA_API_KEY`` set), then Tavily (if
    ``TAVILY_API_KEY`` set). Raises ``EscalationRequired`` if no search
    provider returns substantive content.

    Preconditions:
        At least one of ``EXA_API_KEY`` or ``TAVILY_API_KEY`` must be set
        in the environment.
    Postconditions:
        Returns a non-empty string of at least
        ``MIN_RESEARCH_CONTENT_LENGTH`` characters containing synthesised
        search results, or raises ``EscalationRequired``.
    """
    logger = get_run_logger()
    content: str | None = None

    # Path 1 — Exa semantic search + LLM synthesis
    if os.environ.get("EXA_API_KEY"):
        try:
            from exa_py import Exa  # type: ignore[import-not-found]

            emit_progress({"status": "searching with Exa", "agent": "researcher"})
            exa = Exa(api_key=os.environ["EXA_API_KEY"])
            # exa_py uses synchronous HTTP internally. Under langgraph dev,
            # blockbuster intercepts sync socket calls on the event loop.
            # asyncio.to_thread moves the call to a thread pool.
            search_results = await asyncio.to_thread(
                exa.search_and_contents,
                query=task,
                num_results=max_results,
                text=True,
            )
            findings = _format_exa_results(search_results)

            emit_progress({"status": "synthesising findings", "agent": "researcher"})
            prompt = _env.get_template("deep_synth.j2").render(
                task=task,
                findings=findings,
                context=context,
            )
            content = await _ainvoke_text(model_string, prompt)
        except ImportError:
            logger.warning("EXA_API_KEY set but exa-py not installed. Trying Tavily.")
        except Exception as exc:
            logger.warning("Exa search failed: %s — trying Tavily.", exc)

    # Path 2 — Tavily ReAct agent
    if content is None and os.environ.get("TAVILY_API_KEY"):
        try:
            react_agent = _get_react_agent(model_string)
            emit_progress({"status": "searching with Tavily", "agent": "researcher"})
            react_task = _env.get_template("deep_react.j2").render(
                task=task,
                context=context,
            )
            result = await react_agent.ainvoke(
                {"messages": [{"role": "user", "content": react_task}]}
            )
            content = _last_ai_message(result.get("messages", []))
        except ImportError:
            logger.warning("TAVILY_API_KEY set but langchain-community not installed.")

    # Quality gate — no LLM-only fallback.
    # Enumerate what we tried so the escalation message is actionable
    # for operators diagnosing API-key or service issues.
    providers_tried: list[str] = []
    if os.environ.get("EXA_API_KEY"):
        providers_tried.append("Exa")
    if os.environ.get("TAVILY_API_KEY"):
        providers_tried.append("Tavily")

    if not providers_tried:
        raise EscalationRequired(
            "No search provider configured. Set EXA_API_KEY or "
            "TAVILY_API_KEY. Research requires web search."
        )

    if content is None or not content.strip():
        raise EscalationRequired(
            f"Search providers tried ({', '.join(providers_tried)}) returned "
            f"no results. Check API keys and service availability."
        )

    if len(content.strip()) < MIN_RESEARCH_CONTENT_LENGTH:
        raise EscalationRequired(
            f"Search returned only {len(content.strip())} chars — likely a "
            f"tool error, not research. Providers tried: "
            f"{', '.join(providers_tried)}. "
            f"Preview: {content.strip()[:200]}"
        )

    return content


researcher = agent("researcher")


@researcher(command="fast")
async def researcher_fast(
    task: str, context: list[dict[str, Any]] | None = None
) -> str:
    """Quick research with web search. Fewer search results than deep."""
    emit_progress({"status": "researching (fast)", "agent": "researcher"})

    content = await _search(
        task,
        context or [],
        _model_string(),
        max_results=3,
    )

    await get_catalogue().write(
        content=content.encode(),
        content_type="text/markdown",
        summary=content[:200],
        confidence=0.7,
        completeness="complete",
    )
    return content


@researcher(command="deep")
async def researcher_deep(
    task: str, context: list[dict[str, Any]] | None = None
) -> str:
    """Exhaustive research with web search and LLM synthesis."""
    emit_progress({"status": "starting deep research", "agent": "researcher"})

    content = await _search(
        task,
        context or [],
        _model_string(),
        max_results=10,
    )

    emit_progress({"status": "writing to catalogue", "agent": "researcher"})
    await get_catalogue().write(
        content=content.encode(),
        content_type="text/markdown",
        summary=content[:200],
        confidence=0.85,
        completeness="complete",
    )
    return content
