"""Reference researcher agent — query synthesis with optional web search.

Two commands:
  - fast: quick LLM-only lookup, no web search (~30s)
  - deep: full research with provider selection at call time:
      1. EXA_API_KEY + exa_py importable  -> Exa semantic search + LLM synthesis
      2. TAVILY_API_KEY + langchain_community importable -> Tavily ReAct agent
      3. otherwise -> LLM-only synthesis with warning

Module-level imports stay LLM-only so `import monet.agents` succeeds
without exa_py or langchain_community installed.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

from monet import agent, emit_progress, get_catalogue, get_run_logger

from .._prompts import extract_text, make_env

_env = make_env(Path(__file__).parent)


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


researcher = agent("researcher")


@researcher(command="fast")
async def researcher_fast(
    task: str, context: list[dict[str, Any]] | None = None
) -> str:
    """Quick LLM-only lookup. No web search. Completes in under ~30s."""
    emit_progress({"status": "researching (fast)", "agent": "researcher"})

    prompt = _env.get_template("fast.j2").render(task=task, context=context or [])
    content = await _ainvoke_text(_model_string(), prompt)

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
    """Exhaustive research with web search and LLM synthesis.

    Selects a search provider at call time based on available API keys.
    Priority: Exa > Tavily > LLM-only.
    """
    logger = get_run_logger()
    model_string = _model_string()

    emit_progress({"status": "starting deep research", "agent": "researcher"})

    content: str | None = None
    used_search = False

    # Path 1 — Exa
    if os.environ.get("EXA_API_KEY"):
        try:
            from exa_py import Exa  # type: ignore[import-not-found]

            emit_progress({"status": "searching with Exa", "agent": "researcher"})
            exa = Exa(api_key=os.environ["EXA_API_KEY"])
            search_results = exa.search_and_contents(
                query=task,
                num_results=10,
                use_autoprompt=True,
                text=True,
            )
            findings = _format_exa_results(search_results)

            emit_progress({"status": "synthesising findings", "agent": "researcher"})
            prompt = _env.get_template("deep_synth.j2").render(
                task=task, findings=findings, context=context or []
            )
            content = await _ainvoke_text(model_string, prompt)
            used_search = True
        except ImportError:
            logger.warning(
                "EXA_API_KEY is set but exa-py is not installed. "
                "Falling back to Tavily or LLM-only."
            )

    # Path 2 — Tavily ReAct agent
    if content is None and os.environ.get("TAVILY_API_KEY"):
        try:
            react_agent = _get_react_agent(model_string)
            emit_progress({"status": "searching with Tavily", "agent": "researcher"})
            # Render context-aware task for the ReAct agent.
            react_task = _env.get_template("deep_react.j2").render(
                task=task, context=context or []
            )
            result = await react_agent.ainvoke(
                {"messages": [{"role": "user", "content": react_task}]}
            )
            content = _last_ai_message(result.get("messages", []))
            used_search = bool(content)
        except ImportError:
            logger.warning(
                "TAVILY_API_KEY is set but langchain-community is not installed. "
                "Falling back to LLM-only."
            )

    # Path 3 — LLM-only synthesis
    if content is None or not content.strip():
        if not os.environ.get("EXA_API_KEY") and not os.environ.get("TAVILY_API_KEY"):
            logger.warning(
                "No search provider configured. Set EXA_API_KEY (preferred) "
                "or TAVILY_API_KEY for web search. Using LLM-only synthesis."
            )
        emit_progress({"status": "synthesising (LLM-only)", "agent": "researcher"})
        prompt = _env.get_template("llm_only.j2").render(
            task=task, context=context or []
        )
        content = await _ainvoke_text(model_string, prompt)

    emit_progress({"status": "writing to catalogue", "agent": "researcher"})
    await get_catalogue().write(
        content=content.encode(),
        content_type="text/markdown",
        summary=content[:200],
        confidence=0.85 if used_search else 0.6,
        completeness="complete",
    )
    return content
