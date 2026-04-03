"""Tests for the LLM-backed social media content example.

Unit tests mock LLM providers to test SDK helper integration.
Integration tests use real LLM calls (require API keys).
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monet._registry import default_registry
from monet._stubs import set_catalogue_client
from monet._types import AgentRunContext, SignalType
from monet.catalogue._memory import InMemoryCatalogueClient
from monet.orchestration._state import has_signal


@pytest.fixture(autouse=True)
def _clean_registry_and_catalogue(monkeypatch: pytest.MonkeyPatch):
    """Isolate agent registrations and catalogue per test."""
    set_catalogue_client(InMemoryCatalogueClient())
    # Set fake API keys so os.environ["..."] doesn't raise KeyError
    # when the mocked constructor is called. Real keys override in
    # integration tests.
    for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.setenv(key, os.environ.get(key, "fake-test-key"))
    with default_registry.registry_scope():
        import importlib

        from .. import agents

        importlib.reload(agents)
        yield


def _mock_ai_message(content: str) -> MagicMock:
    """Create a mock AIMessage with the given content."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []
    return msg


def _mock_gemini(response_content: str) -> AsyncMock:
    """Create a mock Gemini model that returns canned content."""
    mock = AsyncMock()
    mock.ainvoke.return_value = _mock_ai_message(response_content)
    mock.bind_tools = MagicMock(return_value=mock)
    return mock


def _mock_groq(response_content: str) -> AsyncMock:
    """Create a mock Groq model that returns canned content."""
    mock = AsyncMock()
    mock.ainvoke.return_value = _mock_ai_message(response_content)
    return mock


# ---------------------------------------------------------------------------
# Unit tests — mocked LLM
# ---------------------------------------------------------------------------


@patch("examples.social_media_llm.agents.planner.ChatGoogleGenerativeAI")
async def test_planner_triage_mocked(mock_cls: MagicMock) -> None:
    """Planner triage returns valid JSON classification."""
    triage_response = json.dumps(
        {
            "complexity": "complex",
            "suggested_agents": ["sm-researcher", "sm-writer"],
            "requires_planning": True,
        }
    )
    mock_cls.return_value = _mock_gemini(triage_response)

    handler = default_registry.lookup("sm-planner", "fast")
    assert handler is not None
    ctx = AgentRunContext(task="Write about AI", agent_id="sm-planner")
    result = await handler(ctx)

    assert result.success is True
    parsed = json.loads(result.output)
    assert parsed["complexity"] == "complex"


@patch("examples.social_media_llm.agents.planner.ChatGoogleGenerativeAI")
async def test_planner_plan_mocked(mock_cls: MagicMock) -> None:
    """Planner plan produces a work brief and writes artifact."""
    brief = {
        "goal": "Create content about AI",
        "in_scope": ["Twitter"],
        "out_of_scope": [],
        "quality_criteria": {},
        "constraints": {},
        "is_sensitive": False,
        "phases": [{"name": "Draft", "waves": [{"items": []}]}],
        "assumptions": [],
    }
    mock_cls.return_value = _mock_gemini(json.dumps(brief))

    handler = default_registry.lookup("sm-planner", "plan")
    assert handler is not None
    ctx = AgentRunContext(task="Write about AI", agent_id="sm-planner")
    result = await handler(ctx)

    assert result.success is True
    parsed = json.loads(result.output)
    assert parsed["goal"] == "Create content about AI"


@patch("examples.social_media_llm.agents.planner.ChatGoogleGenerativeAI")
async def test_planner_sensitive_topic(mock_cls: MagicMock) -> None:
    """Planner raises NeedsHumanReview for sensitive topics."""
    brief = {
        "goal": "Content about health supplements",
        "is_sensitive": True,
        "phases": [],
        "assumptions": [],
    }
    mock_cls.return_value = _mock_gemini(json.dumps(brief))

    handler = default_registry.lookup("sm-planner", "plan")
    assert handler is not None
    ctx = AgentRunContext(task="Health supplements", agent_id="sm-planner")
    result = await handler(ctx)

    assert result.success is False
    assert has_signal(result.signals, SignalType.NEEDS_HUMAN_REVIEW)


@patch("examples.social_media_llm.agents.writer.ChatGoogleGenerativeAI")
async def test_writer_mocked(mock_cls: MagicMock) -> None:
    """Writer generates content and writes artifact."""
    mock_cls.return_value = _mock_gemini("AI is transforming marketing. #AI")

    handler = default_registry.lookup("sm-writer", "deep")
    assert handler is not None
    ctx = AgentRunContext(
        task="Write Twitter post about AI",
        agent_id="sm-writer",
    )
    result = await handler(ctx)

    assert result.success is True
    assert "AI" in result.output


@patch("examples.social_media_llm.agents.qa.ChatGroq")
async def test_qa_pass_mocked(mock_cls: MagicMock) -> None:
    """QA returns pass verdict for good content."""
    verdict = {"verdict": "pass", "confidence": 0.9, "notes": "Good quality"}
    mock_cls.return_value = _mock_groq(json.dumps(verdict))

    handler = default_registry.lookup("sm-qa", "fast")
    assert handler is not None
    ctx = AgentRunContext(task="Review content", agent_id="sm-qa")
    result = await handler(ctx)

    assert result.success is True
    parsed = json.loads(result.output)
    assert parsed["verdict"] == "pass"
    assert result.signals == []


@patch("examples.social_media_llm.agents.qa.ChatGroq")
async def test_qa_low_confidence_semantic_error(mock_cls: MagicMock) -> None:
    """QA raises SemanticError when confidence < 0.5."""
    verdict = {"verdict": "fail", "confidence": 0.3, "notes": "Very poor"}
    mock_cls.return_value = _mock_groq(json.dumps(verdict))

    handler = default_registry.lookup("sm-qa", "fast")
    assert handler is not None
    ctx = AgentRunContext(task="Review content", agent_id="sm-qa")
    result = await handler(ctx)

    assert result.success is False
    assert has_signal(result.signals, SignalType.SEMANTIC_ERROR)


@patch("examples.social_media_llm.agents.qa.ChatGroq")
async def test_qa_marginal_emits_signal(mock_cls: MagicMock) -> None:
    """QA emits NeedsHumanReview signal for marginal confidence."""
    verdict = {"verdict": "pass", "confidence": 0.6, "notes": "Marginal"}
    mock_cls.return_value = _mock_groq(json.dumps(verdict))

    handler = default_registry.lookup("sm-qa", "fast")
    assert handler is not None
    ctx = AgentRunContext(task="Review content", agent_id="sm-qa")
    result = await handler(ctx)

    # Non-fatal: success=True but carries a signal
    assert result.success is True
    assert has_signal(result.signals, SignalType.NEEDS_HUMAN_REVIEW)
    parsed = json.loads(result.output)
    assert parsed["verdict"] == "pass"


async def test_publisher_cli_success() -> None:
    """Publisher CLI subprocess runs and returns result."""
    handler = default_registry.lookup("sm-publisher", "publish")
    assert handler is not None
    ctx = AgentRunContext(
        task="Publish Twitter post",
        agent_id="sm-publisher",
        run_id="test-pub",
    )
    result = await handler(ctx)

    assert result.success is True
    parsed = json.loads(result.output)
    assert parsed["status"] == "published"
    assert parsed["platform"] == "twitter"


# ---------------------------------------------------------------------------
# Integration tests — real LLM (require API keys)
# ---------------------------------------------------------------------------

_has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
_has_groq = bool(os.environ.get("GROQ_API_KEY"))
_has_tavily = bool(os.environ.get("TAVILY_API_KEY"))


@pytest.mark.llm_integration
@pytest.mark.skipif(not _has_gemini, reason="GEMINI_API_KEY not set")
async def test_planner_real_llm() -> None:
    """Real Gemini call for triage classification."""
    handler = default_registry.lookup("sm-planner", "fast")
    assert handler is not None
    ctx = AgentRunContext(
        task="Create social media content about AI trends in 2026",
        agent_id="sm-planner",
    )
    result = await handler(ctx)
    assert result.success is True
    parsed = json.loads(result.output)
    assert parsed["complexity"] in ("simple", "bounded", "complex")


@pytest.mark.llm_integration
@pytest.mark.skipif(
    not (_has_gemini and _has_tavily),
    reason="GEMINI_API_KEY or TAVILY_API_KEY not set",
)
async def test_researcher_real_tavily() -> None:
    """Real Tavily search + Gemini synthesis."""
    handler = default_registry.lookup("sm-researcher", "deep")
    assert handler is not None
    ctx = AgentRunContext(
        task="AI trends in social media marketing 2026",
        agent_id="sm-researcher",
        trace_id="test-trace",
    )
    result = await handler(ctx)
    assert result.success is True
    assert len(result.output) > 100


@pytest.mark.llm_integration
@pytest.mark.skipif(not _has_groq, reason="GROQ_API_KEY not set")
async def test_qa_real_llm() -> None:
    """Real Groq call for QA evaluation."""
    handler = default_registry.lookup("sm-qa", "fast")
    assert handler is not None
    ctx = AgentRunContext(
        task="Review this Twitter post about AI marketing trends",
        agent_id="sm-qa",
    )
    result = await handler(ctx)
    assert result.success is True
    parsed = json.loads(result.output)
    assert parsed["verdict"] in ("pass", "fail")
    assert 0.0 <= parsed["confidence"] <= 1.0
