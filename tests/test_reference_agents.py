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

from monet.artifacts import InMemoryArtifactClient, configure_artifacts
from monet.core.manifest import default_manifest
from monet.core.registry import default_registry  # internal: registry_scope fixture
from monet.orchestration import invoke_agent
from monet.types import SignalType


@pytest.fixture(autouse=True)
def clean_registry_and_artifacts() -> Any:
    configure_artifacts(InMemoryArtifactClient())
    with default_registry.registry_scope(), default_manifest.manifest_scope():
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
    configure_artifacts(None)


def _mock(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(return_value=AIMessage(content=content))
    return mock


# Researcher quality gate requires >= 500 chars from search synthesis.
_LONG_RESEARCH = "# Research Findings\n" + "Key insight about the topic. " * 30


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
    payload = (
        '{"goal": "Test goal",'
        ' "nodes": ['
        '{"id": "research", "depends_on": [],'
        ' "agent_id": "researcher", "command": "deep",'
        ' "task": "research it"}'
        "],"
        ' "is_sensitive": false}'
    )
    with patch("monet.agents.planner._get_model", return_value=_mock(payload)):
        result = await invoke_agent("planner", command="plan", task="plan a thing")
    assert result.success
    # Output is now a dict with artifact id + routing skeleton.
    assert isinstance(result.output, dict)
    assert "work_brief_artifact_id" in result.output
    assert "routing_skeleton" in result.output
    skeleton = result.output["routing_skeleton"]
    assert skeleton["goal"] == "Test goal"
    assert len(skeleton["nodes"]) == 1
    assert skeleton["nodes"][0]["id"] == "research"
    # Routing skeleton does NOT carry task content.
    assert "task" not in skeleton["nodes"][0]
    # Planner registers a keyed artifact.
    assert len(result.artifacts) == 1
    assert result.artifacts[0].get("key") == "work_brief"


async def test_planner_plan_sensitive_raises_human_review() -> None:
    payload = (
        '{"goal": "Sensitive",'
        ' "nodes": ['
        '{"id": "review", "depends_on": [],'
        ' "agent_id": "qa", "command": "fast",'
        ' "task": "review"}'
        "],"
        ' "is_sensitive": true}'
    )
    with patch("monet.agents.planner._get_model", return_value=_mock(payload)):
        result = await invoke_agent("planner", command="plan", task="health advice")
    assert not result.success
    assert result.has_signal(SignalType.NEEDS_HUMAN_REVIEW)


async def test_researcher_fast_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """researcher/fast uses web search (fewer results than deep)."""
    pytest.importorskip("exa_py")
    from unittest.mock import MagicMock

    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    fake_result = MagicMock()
    fake_result.results = [
        MagicMock(title="T", url="https://ex.com", text="content snippet")
    ]
    with patch("exa_py.Exa") as mock_exa_cls:
        mock_exa_cls.return_value.search_and_contents.return_value = fake_result
        with patch(
            "monet.agents.researcher._get_model",
            return_value=_mock(_LONG_RESEARCH),
        ):
            result = await invoke_agent("researcher", command="fast", task="topic")
    assert result.success
    assert isinstance(result.output, str) and "Research Findings" in result.output


async def test_researcher_deep_exa_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """EXA_API_KEY + exa_py installed -> Exa search + LLM synthesis."""
    pytest.importorskip("exa_py")
    from unittest.mock import MagicMock

    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    fake_result = MagicMock()
    fake_result.results = [
        MagicMock(title="T", url="https://ex.com", text="content snippet")
    ]
    with patch("exa_py.Exa") as mock_exa_cls:
        mock_exa_cls.return_value.search_and_contents.return_value = fake_result
        with patch(
            "monet.agents.researcher._get_model",
            return_value=_mock(_LONG_RESEARCH),
        ):
            result = await invoke_agent("researcher", command="deep", task="q")
    assert result.success
    assert isinstance(result.output, str) and "Research Findings" in result.output


async def test_researcher_deep_tavily_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """TAVILY_API_KEY set (no Exa) -> Tavily ReAct agent path."""
    from unittest.mock import MagicMock

    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    fake_agent = MagicMock()
    fake_agent.ainvoke = AsyncMock(
        return_value={"messages": [AIMessage(content=_LONG_RESEARCH)]}
    )
    with patch("monet.agents.researcher._get_react_agent", return_value=fake_agent):
        result = await invoke_agent("researcher", command="deep", task="q")
    assert result.success
    assert isinstance(result.output, str) and "Research Findings" in result.output


async def test_researcher_deep_escalates_without_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No search keys -> ESCALATION_REQUIRED signal, not LLM-only fallback."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = await invoke_agent("researcher", command="deep", task="q")
    assert not result.success
    assert result.has_signal(SignalType.ESCALATION_REQUIRED)
    reason = result.signals[0]["reason"]
    assert "search provider" in reason.lower()


async def test_researcher_fast_escalates_without_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """researcher/fast also requires search — no LLM-only research."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = await invoke_agent("researcher", command="fast", task="q")
    assert not result.success
    assert result.has_signal(SignalType.ESCALATION_REQUIRED)
    reason = result.signals[0]["reason"]
    assert "search provider" in reason.lower()


async def test_researcher_deep_escalates_on_thin_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search returning an apology/error message triggers escalation."""
    from unittest.mock import MagicMock

    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    fake_agent = MagicMock()
    fake_agent.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content="I apologize, Error 432 occurred.")]
        }
    )
    with patch("monet.agents.researcher._get_react_agent", return_value=fake_agent):
        result = await invoke_agent("researcher", command="deep", task="q")
    assert not result.success
    assert result.has_signal(SignalType.ESCALATION_REQUIRED)
    reason = result.signals[0]["reason"]
    assert "chars" in reason


async def test_researcher_escalation_includes_provider_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalation message names which providers were tried (SRE aid)."""
    from unittest.mock import MagicMock

    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    fake_agent = MagicMock()
    fake_agent.ainvoke = AsyncMock(return_value={"messages": []})

    with patch("monet.agents.researcher._get_react_agent", return_value=fake_agent):
        result = await invoke_agent("researcher", command="deep", task="q")
    assert not result.success
    assert result.has_signal(SignalType.ESCALATION_REQUIRED)
    assert "Tavily" in result.signals[0]["reason"]


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
