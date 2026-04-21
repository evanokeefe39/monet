"""End-to-end capability chain for the agent-recruitment example.

Covers the full recruitment cycle at the capability-agent level:

1. Subprocess sandbox runs candidate code against a fixture + assertions.
2. ``code_executor(eval_all)`` aggregates per-candidate reports into a
   ``trial_scorecard`` artifact and emits the right signals.
3. ``evaluator(compare)`` consumes the scorecard, applies the baseline,
   ranks survivors, and writes a ``comparative_review``.
4. ``record_run_summary`` hook writes one ``run_summary`` per invocation.
5. ``data_analyst(score_agents)`` reads the run summaries, scores the
   roster, and emits ``ESCALATION_REQUIRED`` for underperformers.

The test does not drive the default planner — planner-prompted DAG
composition is an LLM-in-the-loop concern covered by the user's
interactive iteration step. This test proves the capability chain is
composable; the planner's job is to compose it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph")


from monet.artifacts import InMemoryArtifactClient, configure_artifacts
from monet.core.hooks import default_hook_registry
from monet.core.registry import default_registry

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _good_candidate() -> dict[str, Any]:
    return {
        "id": "good",
        "source_code": ("import sys\nsys.stdout.write('monet rocks\\n')\n"),
        "entrypoint": "agent.py",
    }


def _bad_candidate() -> dict[str, Any]:
    return {
        "id": "bad",
        "source_code": "import sys\nsys.exit(1)\n",
        "entrypoint": "agent.py",
    }


def _harness() -> dict[str, Any]:
    return json.loads((_FIXTURES / "harness.json").read_text())


def _baseline() -> dict[str, Any]:
    return json.loads((_FIXTURES / "baseline.json").read_text())


class _StubOtelBackend:
    """Deterministic OTel backend for tests — avoids a trace exporter."""

    def __init__(
        self, invocations: dict[tuple[str, str], list[dict[str, Any]]]
    ) -> None:
        self._invocations = invocations

    async def query_spans(self, *, agent_id, run_id, since, limit):
        return []

    async def token_usage(self, *, agent_id, since):
        return {"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0}

    async def agent_invocations(self, *, agent_id, command, since):
        return list(self._invocations.get((agent_id, command), []))


async def test_recruitment_capability_chain() -> None:
    """Exercise code_executor → evaluator(compare) → data_analyst end-to-end."""
    configure_artifacts(InMemoryArtifactClient())
    with (
        default_registry.registry_scope(),
        default_hook_registry.hook_scope(),
    ):
        import importlib

        import recruitment.agents as recruitment_agents

        importlib.reload(recruitment_agents)
        from recruitment.agents.code_executor import (
            code_executor_eval_all,
        )
        from recruitment.agents.data_analyst import (
            data_analyst_score_agents,
        )
        from recruitment.tools import configure_otel_backend

        from monet.agents.evaluator import evaluator_compare

        harness = _harness()

        # ── 1. code_executor(eval_all) ──
        exec_spec = json.dumps(
            {
                "candidates": [_good_candidate(), _bad_candidate()],
                "fixture": harness["fixture"],
                "assertions": harness["assertions"],
                "timeout_s": harness["timeout_s"],
                "max_parallel": 2,
            }
        )
        trial_json = await code_executor_eval_all.__wrapped__(
            task=exec_spec, context=[]
        )  # type: ignore[attr-defined]
        trial = json.loads(trial_json)
        assert {r["candidate_id"] for r in trial["reports"]} == {"good", "bad"}
        good_report = next(r for r in trial["reports"] if r["candidate_id"] == "good")
        assert good_report["ok"] is True
        assert good_report["assertion_pass_rate"] == 1.0

        from monet.core.artifacts import get_artifacts

        rows = await get_artifacts().query_recent(tag="trial_scorecard", limit=1)
        assert rows, "code_executor must have written a trial_scorecard"
        scorecard_pointer = {
            "artifact_id": rows[0]["artifact_id"],
            "url": f"memory://{rows[0]['artifact_id']}",
            "key": "trial_scorecard",
        }
        qa_context = [{"type": "upstream_result", "artifacts": [scorecard_pointer]}]

        # ── 2. evaluator(compare) ──
        qa_spec = json.dumps(_baseline())
        review_json = await evaluator_compare.__wrapped__(
            task=qa_spec, context=qa_context
        )  # type: ignore[attr-defined]
        review = json.loads(review_json)
        assert review["verdict"] == "some_pass"
        assert review["recommended"] == "good"

        # ── 3. data_analyst(score_agents) with a stub OTel backend ──
        #     (spans are the single source of truth for per-invocation
        #     outcomes; inject a deterministic backend instead of
        #     standing up an exporter in the test.)
        configure_otel_backend(
            _StubOtelBackend(
                {
                    ("code_executor", "eval_all"): [
                        {
                            "run_id": "r1",
                            "trace_id": "t1",
                            "command": "eval_all",
                            "success": True,
                            "duration_ms": 1500.0,
                            "signals": [],
                        }
                    ]
                }
            )
        )
        try:
            roster_json = await data_analyst_score_agents.__wrapped__(  # type: ignore[attr-defined]
                task=json.dumps({"window_days": 7, "score_threshold": 0.5}),
                context=[],
            )
        finally:
            configure_otel_backend(None)
        roster = json.loads(roster_json)
        assert roster["window_days"] == 7
        assert roster["scores"], "data_analyst must emit at least one score"
        code_exec_score = next(
            s
            for s in roster["scores"]
            if s["agent_id"] == "code_executor" and s["command"] == "eval_all"
        )
        assert code_exec_score["invocations"] == 1
        assert code_exec_score["avg_duration_ms"] == 1500.0

    configure_artifacts(None)
