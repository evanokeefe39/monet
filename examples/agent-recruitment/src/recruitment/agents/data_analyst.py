"""data_analyst — composes query tools over monet's telemetry surfaces.

Two commands, both driven through the tools in ``recruitment.tools``:

- ``query``: thin wrapper over ``artifact_query`` — ad-hoc introspection
  of the artifact index.
- ``score_agents``: enumerates the manifest, then for each agent composes
  ``otel_agent_invocations`` (one row per agent span — success, signals,
  duration), ``otel_token_usage`` (gen_ai.usage.* token totals), and
  ``artifact_query`` (artifact provenance). No intermediate
  ``RunSummary`` artifact — spans and artifacts already carry every
  field an earlier draft of the hook was duplicating.

The agent does not talk to the artifact index or OTel backend directly —
same pattern as ``researcher`` calling out via Exa / Tavily tools.
Swapping in a Postgres tool, an MCP-server tool, or a Langfuse OTel
backend replaces the tool, not the agent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from monet import (
    Signal,
    SignalType,
    agent,
    emit_progress,
    emit_signal,
    get_artifacts,
)
from monet.core.registry import default_registry
from monet.exceptions import SemanticError

from ..schemas import AgentRoster, AgentScore
from ..tools import artifact_query, otel_agent_invocations, otel_token_usage

data_analyst = agent("data_analyst")


def _parse_task(task: str) -> dict[str, Any]:
    if not task.strip():
        return {}
    try:
        spec = json.loads(task)
    except json.JSONDecodeError as exc:
        raise SemanticError(
            type="parse_error",
            message=f"data_analyst task must be JSON: {exc}",
        ) from exc
    if not isinstance(spec, dict):
        raise SemanticError(
            type="parse_error",
            message="data_analyst task must be a JSON object",
        )
    return spec


@data_analyst(command="query")
async def data_analyst_query(
    task: str,
    context: list[dict[str, Any]] | None = None,
) -> str:
    """Return recent artifact metadata matching the given filters.

    Planner contract: ``task`` is a JSON string ``{agent_id?, tag?,
    since?, limit?}``. Thin wrapper around the ``artifact_query`` tool —
    same pattern as ``researcher(fast)`` delegating to Exa / Tavily.
    """
    emit_progress({"status": "querying", "agent": "data_analyst", "command": "query"})
    spec = _parse_task(task)
    rows = await artifact_query.ainvoke(
        {
            "agent_id": spec.get("agent_id"),
            "tag": spec.get("tag"),
            "since": spec.get("since"),
            "limit": int(spec.get("limit") or 100),
        }
    )
    payload = {"count": len(rows), "artifacts": rows}
    return json.dumps(payload, default=str)


def _escalation_rate(invocations: list[dict[str, Any]]) -> float:
    if not invocations:
        return 0.0
    escalations = sum(
        1
        for inv in invocations
        for sig in inv.get("signals") or []
        if sig.get("type")
        in {SignalType.ESCALATION_REQUIRED, SignalType.NEEDS_HUMAN_REVIEW}
    )
    return escalations / len(invocations)


def _avg_duration(invocations: list[dict[str, Any]]) -> float:
    if not invocations:
        return 0.0
    return sum(float(inv.get("duration_ms", 0.0)) for inv in invocations) / len(
        invocations
    )


def _success_rate(invocations: list[dict[str, Any]]) -> float:
    if not invocations:
        return 0.0
    return sum(1 for inv in invocations if inv.get("success")) / len(invocations)


def _composite_score(
    success_rate: float,
    esc_rate: float,
    avg_dur_ms: float,
    tokens_per_call: float,
) -> float:
    """Higher is better. Combines success, escalation rate, latency, tokens.

    Weights are intentionally illustrative, not productised — see the
    roadmap "Reference agent quality pass" guardrail.
    """
    latency = max(0.0, 1.0 - min(avg_dur_ms, 60_000.0) / 60_000.0)
    token_factor = max(0.0, 1.0 - min(tokens_per_call, 20_000.0) / 20_000.0)
    return (
        0.45 * success_rate
        + 0.20 * latency
        + 0.15 * (1.0 - esc_rate)
        + 0.20 * token_factor
    )


@data_analyst(command="score_agents")
async def data_analyst_score_agents(
    task: str,
    context: list[dict[str, Any]] | None = None,
) -> str:
    """Score every registered agent over the last ``window_days`` days.

    Planner contract: ``task`` is a JSON string ``{window_days?: int,
    score_threshold?: float}`` (defaults: 7 days, 0.5 threshold). Sources:
    ``otel_agent_invocations`` (success, signals, duration per invocation),
    ``otel_token_usage`` (token totals), and ``artifact_query`` if artifact
    provenance is desired. No duplicate ``RunSummary`` artifact — spans
    are the single source of truth for per-invocation outcomes.
    Emits ``ESCALATION_REQUIRED`` for flagged underperformers and writes
    one ``roster_scorecard`` artifact.
    """
    emit_progress(
        {"status": "scoring", "agent": "data_analyst", "command": "score_agents"}
    )
    spec = _parse_task(task)
    window_days = int(spec.get("window_days") or 7)
    threshold = float(spec.get("score_threshold") or 0.5)
    since = (datetime.now(tz=UTC) - timedelta(days=window_days)).isoformat()

    roster = default_registry.registered_agents(with_docstrings=True)

    scores: list[AgentScore] = []
    for capability in roster:
        agent_id = capability.agent_id
        command = capability.command

        try:
            invocations: list[dict[str, Any]] = await otel_agent_invocations.ainvoke(
                {"agent_id": agent_id, "command": command, "since": since}
            )
        except Exception:
            invocations = []

        try:
            token_totals = await otel_token_usage.ainvoke(
                {"agent_id": agent_id, "since": since}
            )
        except Exception:
            token_totals = {"total_tokens": 0.0}

        success_rate = _success_rate(invocations)
        esc_rate = _escalation_rate(invocations)
        avg_dur = _avg_duration(invocations)
        total_tokens = float(token_totals.get("total_tokens") or 0.0)
        tokens_per_call = total_tokens / len(invocations) if invocations else 0.0
        score = _composite_score(success_rate, esc_rate, avg_dur, tokens_per_call)
        flagged = bool(invocations) and score < threshold
        reason = ""
        if flagged:
            reason = (
                f"score {score:.2f} < {threshold:.2f} "
                f"(ok={success_rate:.2f}, esc={esc_rate:.2f}, "
                f"dur={avg_dur:.0f}ms, tokens/call={tokens_per_call:.0f})"
            )
        scores.append(
            AgentScore(
                agent_id=agent_id,
                command=command,
                invocations=len(invocations),
                escalation_rate=esc_rate,
                avg_duration_ms=avg_dur,
                score=round(score, 4),
                flagged=flagged,
                reason=reason,
            )
        )

    roster = AgentRoster(window_days=window_days, scores=scores)

    flagged_count = sum(1 for s in scores if s.flagged)
    if flagged_count:
        escalation_reason = (
            f"{flagged_count} agent(s) below score threshold {threshold:.2f}"
        )
        emit_signal(
            Signal(
                type=SignalType.ESCALATION_REQUIRED,
                reason=escalation_reason,
                metadata={
                    "flagged_ids": [
                        f"{s.agent_id}({s.command})" for s in scores if s.flagged
                    ],
                },
            )
        )

    await get_artifacts().write(
        json.dumps(roster.model_dump()).encode("utf-8"),
        content_type="application/json",
        summary=f"roster_scorecard: {len(scores)} agents ({flagged_count} flagged)",
        confidence=1.0,
        completeness="complete",
        tags={"roster_scorecard": True, "window_days": window_days},
        key="roster_scorecard",
    )

    return json.dumps(roster.model_dump())
