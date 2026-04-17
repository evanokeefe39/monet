"""Shared pydantic schemas for the recruitment example.

Capability agents (``code_executor``, ``data_analyst``) use these to
keep wire shapes explicit. Per-invocation telemetry is sourced from OTel
spans + the artifact index — there is no ``RunSummary`` schema because
that data already lives in those two authoritative stores.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CandidatePattern(BaseModel):
    """One candidate agent/pattern produced by the discovery stage."""

    id: str
    source_code: str
    entrypoint: str = "agent.py"
    rationale: str = ""


class CandidateBrief(BaseModel):
    """Output of the ``researcher`` discovery run."""

    goal: str
    candidates: list[CandidatePattern]


class ExecutionReport(BaseModel):
    """Per-candidate outcome from ``code_executor``."""

    candidate_id: str
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: float = 0.0
    assertion_pass_rate: float = 0.0
    events: list[dict[str, Any]] = []


class TrialScorecard(BaseModel):
    """Aggregate written by ``code_executor(eval_all)``."""

    reports: list[ExecutionReport]


class AgentScore(BaseModel):
    """Per-(agent_id, command) score produced by ``data_analyst(score_agents)``."""

    agent_id: str
    command: str
    invocations: int
    escalation_rate: float
    avg_duration_ms: float
    score: float
    flagged: bool = False
    reason: str = ""


class AgentRoster(BaseModel):
    """Roll-up written by ``data_analyst(score_agents)``."""

    window_days: int
    scores: list[AgentScore]


# RunSummary intentionally absent.
#
# Earlier iterations of this example shipped a per-invocation
# ``RunSummary`` artifact written by an after_agent hook. That was a
# denormalised duplicate of data already captured at two authoritative
# sources:
#
# - OTel agent span: agent.id, agent.command, agent.success, start /
#   end time, plus signal events emitted by ``emit_signal``. Child
#   ``gen_ai.usage.*`` spans carry token counts.
# - Artifact index: one row per artifact written, with agent_id,
#   run_id, trace_id, created_at, and tags.
#
# ``data_analyst`` composes ``otel_query`` + ``artifact_query`` to
# score the roster. No hook, no summary artifact, no third data plane
# to keep in sync.
