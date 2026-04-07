"""Tests for src/monet/agents/ — five reference agents.

Models are patched at _get_model so no provider package or API key is needed
beyond langchain_core for AIMessage. The clean_registry fixture imports
monet.agents inside registry_scope() so @agent decorators fire inside scope.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage

from monet._registry import default_registry  # internal: registry_scope fixture
from monet.catalogue import InMemoryCatalogueClient, configure_catalogue
from monet.orchestration import invoke_agent
from monet.types import SignalType


@pytest.fixture(autouse=True)
def clean_registry_and_catalogue() -> Any:
    configure_catalogue(InMemoryCatalogueClient())
    with default_registry.registry_scope():
        import importlib

        import monet.agents.planner
        import monet.agents.publisher
        import monet.agents.qa
        import monet.agents.researcher
        import monet.agents.writer

        for mod in (
            monet.agents.planner,
            monet.agents.researcher,
            monet.agents.writer,
            monet.agents.qa,
            monet.agents.publisher,
        ):
            importlib.reload(mod)
        yield
    configure_catalogue(None)


def _mock(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(return_value=AIMessage(content=content))
    return mock


async def test_planner_triage_returns_json() -> None:
    payload = (
        '{"complexity": "complex", "suggested_agents": ["writer"],'
        ' "requires_planning": true}'
    )
    with patch("monet.agents.planner._get_model", return_value=_mock(payload)):
        result = await invoke_agent("planner", command="fast", task="Write something")
    assert result.success
    assert isinstance(result.output, str) and "complex" in result.output


async def test_planner_plan_returns_brief() -> None:
    with patch(
        "monet.agents.planner._get_model",
        return_value=_mock(
            '{"goal": "Test goal", "phases": [], "is_sensitive": false}'
        ),
    ):
        result = await invoke_agent("planner", command="plan", task="plan a thing")
    assert result.success
    assert isinstance(result.output, str) and "Test goal" in result.output


async def test_planner_plan_sensitive_raises_human_review() -> None:
    with patch(
        "monet.agents.planner._get_model",
        return_value=_mock('{"goal": "Sensitive", "phases": [], "is_sensitive": true}'),
    ):
        result = await invoke_agent("planner", command="plan", task="health advice")
    assert not result.success
    assert result.has_signal(SignalType.NEEDS_HUMAN_REVIEW)


async def test_researcher_returns_content() -> None:
    with patch(
        "monet.agents.researcher._get_model",
        return_value=_mock("# Findings\nKey insight."),
    ):
        result = await invoke_agent("researcher", command="deep", task="topic")
    assert result.success
    assert isinstance(result.output, str) and "Findings" in result.output


async def test_writer_returns_content() -> None:
    with patch(
        "monet.agents.writer._get_model", return_value=_mock("Polished text here.")
    ):
        result = await invoke_agent("writer", command="deep", task="write")
    assert result.success
    assert isinstance(result.output, str) and "Polished" in result.output


async def test_qa_low_confidence_emits_signal() -> None:
    with patch(
        "monet.agents.qa._get_model",
        return_value=_mock(
            '{"verdict": "marginal", "confidence": 0.4, "notes": "weak"}'
        ),
    ):
        result = await invoke_agent("qa", command="fast", task="evaluate this")
    assert result.success
    assert result.has_signal(SignalType.LOW_CONFIDENCE)


async def test_qa_fail_emits_revision_suggested() -> None:
    with patch(
        "monet.agents.qa._get_model",
        return_value=_mock(
            '{"verdict": "fail", "confidence": 0.7, "notes": "needs work"}'
        ),
    ):
        result = await invoke_agent("qa", command="fast", task="evaluate this")
    assert result.success
    assert result.has_signal(SignalType.REVISION_SUGGESTED)


async def test_publisher_returns_content() -> None:
    with patch(
        "monet.agents.publisher._get_model",
        return_value=_mock("# Published\nFinal output."),
    ):
        result = await invoke_agent("publisher", command="publish", task="content")
    assert result.success
    assert isinstance(result.output, str) and "Published" in result.output
