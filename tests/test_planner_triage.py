# mypy: disable-error-code="attr-defined"
"""Planner triage: structured output + few-shot classification (I7)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("langchain_core")

from monet.core.manifest import default_manifest
from monet.core.registry import default_registry


@pytest.fixture(autouse=True)
def _reset() -> Any:
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


def _structured_mock(result: Any) -> AsyncMock:
    """Return a mock whose .with_structured_output(...).ainvoke(...) gives result."""
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(return_value=result)
    mock.with_structured_output = lambda schema: mock
    return mock


async def test_structured_output_comparative_analysis_is_complex() -> None:
    # Re-import after the fixture's module reload so the class identity
    # matches the one planner_fast uses in its isinstance check.
    from monet.agents.planner import TriageResult

    expected = TriageResult(
        complexity="complex",
        suggested_agents=["researcher", "writer", "qa"],
        requires_planning=True,
    )
    with patch(
        "monet.agents.planner._get_model",
        return_value=_structured_mock(expected),
    ):
        from monet.agents.planner import planner_fast as fast_impl

        raw = await fast_impl.__wrapped__(
            "Compare React, Vue, and Svelte; recommend one for a greenfield SaaS.",
            None,
        )
    payload = json.loads(raw)
    assert payload["complexity"] == "complex"
    assert payload["requires_planning"] is True
    assert "writer" in payload["suggested_agents"]


async def test_structured_output_three_sentence_brief_is_bounded() -> None:
    from monet.agents.planner import TriageResult

    expected = TriageResult(
        complexity="bounded",
        suggested_agents=["writer"],
        requires_planning=False,
    )
    with patch(
        "monet.agents.planner._get_model",
        return_value=_structured_mock(expected),
    ):
        from monet.agents.planner import planner_fast as fast_impl

        raw = await fast_impl.__wrapped__(
            "Write a 3-sentence product pitch for a fitness tracker app.",
            None,
        )
    payload = json.loads(raw)
    assert payload["complexity"] == "bounded"
    assert payload["suggested_agents"] == ["writer"]


async def test_triage_result_rejects_invalid_complexity() -> None:
    from pydantic import ValidationError

    from monet.agents.planner import TriageResult

    with pytest.raises(ValidationError):
        TriageResult(
            complexity="medium",  # type: ignore[arg-type]
            suggested_agents=[],
            requires_planning=False,
        )


def test_planner_fast_registered() -> None:
    from monet.agents.planner import planner_fast

    assert planner_fast is not None
