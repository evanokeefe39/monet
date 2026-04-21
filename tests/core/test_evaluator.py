"""Tests for the ``evaluator(compare)`` baseline + comparative ranking command.

Drives the agent through the real ``invoke_agent`` + queue + registry
path (via the autouse ``_queue_worker`` fixture in ``conftest.py``) so
decorator behaviour is exercised: context vars, artifact collection,
signal emission, and ``AgentResult`` wrapping all run.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest

from monet import SignalType
from monet.artifacts import InMemoryArtifactClient, configure_artifacts
from monet.core.artifacts import get_artifacts
from monet.core.registry import default_registry
from monet.orchestration import invoke_agent


@pytest.fixture(autouse=True)
def _evaluator_scope() -> Any:
    """Reload evaluator inside a scoped registry so the decorator registrations
    auto-revert after each test — prevents global pollution that would
    collide with declarative-config tests downstream.
    """
    with default_registry.registry_scope():
        import monet.agents.evaluator as evaluator_module

        importlib.reload(evaluator_module)
        yield


@pytest.fixture(autouse=True)
def _artifact_backend() -> Any:
    configure_artifacts(InMemoryArtifactClient())
    yield
    configure_artifacts(None)


async def _install_scorecard(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Write a TrialScorecard artifact, return a pointer dict."""
    pointer = await get_artifacts().write(
        json.dumps({"reports": reports}).encode(),
        content_type="application/json",
        summary="trial",
        confidence=1.0,
        completeness="complete",
        tags={"trial_scorecard": True},
        key="trial_scorecard",
    )
    return {k: pointer[k] for k in pointer}  # type: ignore[literal-required]


def _baseline_spec() -> str:
    return json.dumps(
        {
            "baseline": {"assertion_pass_rate": 0.8, "max_duration_ms": 30_000},
            "criteria": ["correctness", "cost"],
        }
    )


async def _invoke_evaluator(reports: list[dict[str, Any]]) -> Any:
    """Install a scorecard + invoke evaluator(compare) via the real queue path."""
    pointer = await _install_scorecard(reports)
    context = [{"type": "upstream_result", "artifacts": [pointer]}]
    return await invoke_agent(
        "evaluator",
        command="compare",
        task=_baseline_spec(),
        context=context,
    )


async def test_evaluator_all_pass() -> None:
    result = await _invoke_evaluator(
        [
            {
                "candidate_id": "a",
                "ok": True,
                "assertion_pass_rate": 0.9,
                "duration_ms": 1000,
            },
            {
                "candidate_id": "b",
                "ok": True,
                "assertion_pass_rate": 0.95,
                "duration_ms": 500,
            },
        ]
    )
    assert result.success is True
    review = json.loads(result.output)  # type: ignore[arg-type]
    assert review["verdict"] == "all_pass"
    assert review["recommended"] == "b"
    assert not result.has_signal(SignalType.PARTIAL_RESULT)
    assert not result.has_signal(SignalType.ESCALATION_REQUIRED)


async def test_evaluator_some_pass_emits_partial_result() -> None:
    result = await _invoke_evaluator(
        [
            {
                "candidate_id": "good",
                "ok": True,
                "assertion_pass_rate": 0.9,
                "duration_ms": 1000,
            },
            {
                "candidate_id": "slow",
                "ok": True,
                "assertion_pass_rate": 0.9,
                "duration_ms": 50_000,
            },
        ]
    )
    assert result.success is True
    review = json.loads(result.output)  # type: ignore[arg-type]
    assert review["verdict"] == "some_pass"
    assert review["recommended"] == "good"
    partial = result.get_signal(SignalType.PARTIAL_RESULT)
    assert partial is not None
    assert partial["metadata"]["pass_count"] == 1
    assert partial["metadata"]["total"] == 2


async def test_evaluator_none_pass_emits_escalation_required() -> None:
    result = await _invoke_evaluator(
        [
            {
                "candidate_id": "x",
                "ok": False,
                "assertion_pass_rate": 0.1,
                "duration_ms": 100,
            },
            {
                "candidate_id": "y",
                "ok": False,
                "assertion_pass_rate": 0.2,
                "duration_ms": 100,
            },
        ]
    )
    review = json.loads(result.output)  # type: ignore[arg-type]
    assert review["verdict"] == "none_pass"
    assert review["recommended"] is None
    escalation = result.get_signal(SignalType.ESCALATION_REQUIRED)
    assert escalation is not None
    assert escalation["metadata"]["total"] == 2


async def test_evaluator_writes_comparative_review_artifact() -> None:
    """The agent persists a ``comparative_review``-tagged artifact."""
    result = await _invoke_evaluator(
        [
            {
                "candidate_id": "a",
                "ok": True,
                "assertion_pass_rate": 0.9,
                "duration_ms": 100,
            }
        ]
    )
    assert result.success is True
    assert any(a.get("key") == "comparative_review" for a in result.artifacts)
    rows = await get_artifacts().query_recent(tag="evaluator_compare")
    assert len(rows) == 1
    assert rows[0]["content_type"] == "application/json"
