"""Evaluator agent — comparative baseline + ranking over trial scorecards.

NOT a general-purpose content QA agent. This agent consumes structured
TrialScorecard artifacts (produced by test-harness agents like
``code_executor``), applies baseline thresholds, and ranks passing
candidates. Use ``qa/fast`` for content quality evaluation.

Self-excludes from its own roster — the evaluator judges other agents'
output, never its own.
"""

from __future__ import annotations

import json
from typing import Any

from monet import (
    Signal,
    SignalType,
    agent,
    emit_progress,
    emit_signal,
    get_artifacts,
    resolve_context,
)
from monet.exceptions import SemanticError

_EVALUATOR_EXCLUDE: tuple[str, ...] = ("evaluator",)

evaluator = agent("evaluator")


def _passes_baseline(
    report: dict[str, Any], baseline: dict[str, Any]
) -> tuple[bool, list[str]]:
    """Check a single candidate report against the baseline thresholds.

    Supported baseline keys:
    - ``assertion_pass_rate``: minimum rate (candidate must be >=).
    - ``max_duration_ms``: maximum duration (candidate must be <=).
    - ``require_ok``: if True, report["ok"] must be truthy.

    Returns (passes, list of gap descriptions).
    """
    gaps: list[str] = []
    threshold = baseline.get("assertion_pass_rate")
    if isinstance(threshold, int | float):
        rate = float(report.get("assertion_pass_rate", 0.0))
        if rate < threshold:
            gaps.append(f"assertion_pass_rate {rate:.2f} < {threshold:.2f}")

    max_dur = baseline.get("max_duration_ms")
    if isinstance(max_dur, int | float):
        dur = float(report.get("duration_ms", 0))
        if dur > max_dur:
            gaps.append(f"duration_ms {dur:.0f} > {max_dur:.0f}")

    if baseline.get("require_ok") and not report.get("ok"):
        gaps.append("report.ok is False")

    return (not gaps, gaps)


def _score_candidate(report: dict[str, Any], criteria: list[str]) -> float:
    """Score a candidate on the listed criteria. Deterministic.

    Weights: correctness via ``assertion_pass_rate`` (0..1 contribution),
    cost via ``duration_ms`` (normalised 0..1, lower = better), clarity
    best-effort via presence of stdout bytes.
    """
    score = 0.0
    if "correctness" in criteria:
        score += float(report.get("assertion_pass_rate", 0.0))
    if "cost" in criteria:
        dur = float(report.get("duration_ms", 0))
        # 0 ms → 1.0, 60 s → 0.0, linear in between.
        score += max(0.0, 1.0 - min(dur, 60_000.0) / 60_000.0)
    if "clarity" in criteria:
        stdout = str(report.get("stdout") or "")
        # 1.0 for non-empty short stdout, 0.5 for very long, 0 for empty.
        if not stdout:
            score += 0.0
        elif len(stdout) < 400:
            score += 1.0
        else:
            score += 0.5
    return score


async def _load_trial_reports(
    context: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Find the upstream TrialScorecard artifact and return its ``reports`` list.

    Looks across the resolved context entries for an artifact whose
    content parses as JSON and contains ``reports``. Returns None when
    no matching upstream artifact is available.
    """
    store = get_artifacts()
    for entry in context:
        for art in entry.get("artifacts") or []:
            art_id = art.get("artifact_id") or art.get("id")
            if not art_id:
                continue
            try:
                content, _meta = await store.read(art_id)
            except Exception:
                continue
            try:
                payload = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            reports = payload.get("reports") if isinstance(payload, dict) else None
            if isinstance(reports, list) and reports:
                return reports
    return None


@evaluator(command="compare")
async def evaluator_compare(
    task: str, context: list[dict[str, Any]] | None = None
) -> str:
    """Baseline + comparative ranking over a set of candidate trial reports.

    Consumes structured TrialScorecard artifacts — NOT free-text content.
    Use ``qa/fast`` for content quality evaluation.

    Contract:
    - ``task`` is a JSON string ``{baseline, criteria, task_context?}`` where
      ``baseline`` describes the minimum-acceptable bar and ``criteria`` names
      the ranking dimensions (e.g. ``["correctness", "cost", "clarity"]``).
    - ``context`` carries upstream_results with artifacts; the agent resolves
      the most recent upstream artifact whose content is a JSON object with a
      ``reports`` list (the ``TrialScorecard`` written by ``code_executor``).

    Emits:
    - ``PARTIAL_RESULT`` when only some candidates clear the baseline.
    - ``ESCALATION_REQUIRED`` when none do (no recommendation possible).

    Writes a ``ComparativeReview`` artifact tagged ``evaluator_compare``.
    """
    emit_progress({"status": "comparing", "agent": "evaluator", "command": "compare"})

    try:
        spec = json.loads(task) if task.strip() else {}
    except json.JSONDecodeError as exc:
        raise SemanticError(
            type="parse_error",
            message=f"evaluator(compare) task must be JSON: {exc}",
        ) from exc

    baseline = spec.get("baseline") or {}
    criteria = spec.get("criteria") or ["correctness", "cost"]
    if not isinstance(baseline, dict) or not isinstance(criteria, list):
        raise SemanticError(
            type="parse_error",
            message="evaluator(compare) expects 'baseline' object and 'criteria' list",
        )

    resolved = await resolve_context(context or [])
    reports = await _load_trial_reports(resolved)
    if not reports:
        raise SemanticError(
            type="missing_input",
            message="evaluator(compare) found no upstream TrialScorecard in context",
        )

    baseline_results: list[dict[str, Any]] = []
    passed: list[tuple[dict[str, Any], float]] = []
    for report in reports:
        candidate_id = str(report.get("candidate_id") or report.get("id") or "")
        passes, gaps = _passes_baseline(report, baseline)
        baseline_results.append(
            {"candidate_id": candidate_id, "passes": passes, "gaps": gaps}
        )
        if passes:
            passed.append((report, _score_candidate(report, criteria)))

    total = len(baseline_results)
    pass_count = sum(1 for r in baseline_results if r["passes"])
    if pass_count == 0:
        verdict = "none_pass"
    elif pass_count < total:
        verdict = "some_pass"
    else:
        verdict = "all_pass"

    passed.sort(key=lambda pair: pair[1], reverse=True)
    ranking: list[dict[str, Any]] = []
    for rank, (report, score) in enumerate(passed, start=1):
        candidate_id = str(report.get("candidate_id") or report.get("id") or "")
        ranking.append(
            {
                "candidate_id": candidate_id,
                "rank": rank,
                "score": round(score, 4),
                "rationale": f"criteria={criteria}",
            }
        )
    recommended = ranking[0]["candidate_id"] if ranking else None

    review = {
        "verdict": verdict,
        "baseline_results": baseline_results,
        "ranking": ranking,
        "recommended": recommended,
    }

    if verdict == "some_pass":
        emit_signal(
            Signal(
                type=SignalType.PARTIAL_RESULT,
                reason=f"{pass_count}/{total} candidates cleared baseline",
                metadata={"pass_count": pass_count, "total": total},
            )
        )
    elif verdict == "none_pass":
        emit_signal(
            Signal(
                type=SignalType.ESCALATION_REQUIRED,
                reason="No candidate cleared the baseline; no recommendation",
                metadata={"total": total},
            )
        )

    store = get_artifacts()
    await store.write(
        json.dumps(review).encode("utf-8"),
        content_type="application/json",
        summary=f"Comparative review: {verdict} ({pass_count}/{total})",
        confidence=1.0,
        completeness="complete",
        tags={"evaluator_compare": True, "comparative_review": True},
        key="comparative_review",
    )

    return json.dumps(review)
