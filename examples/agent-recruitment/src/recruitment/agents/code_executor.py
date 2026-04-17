"""code_executor — subprocess-sandboxed evaluator for candidate agents.

Two commands:

- ``run``: evaluate a single candidate against a fixture + assertions.
  Useful for ad-hoc probing. Writes one ``candidate_report`` artifact.
- ``eval_all``: evaluate a list of candidates against a shared fixture
  + assertions; bounded parallelism via ``asyncio.Semaphore``. Writes
  one ``trial_scorecard`` artifact holding every per-candidate report.

Not a security boundary — candidates run in the worker's Python
interpreter, isolated only to a temporary workspace. See
``recruitment/sandbox.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from monet import Signal, SignalType, agent, emit_progress, emit_signal, get_artifacts
from monet.exceptions import SemanticError

from ..sandbox import run_candidate_in_subprocess
from ..schemas import CandidateBrief, CandidatePattern, ExecutionReport, TrialScorecard

code_executor = agent("code_executor")


def _parse_task(task: str) -> dict[str, Any]:
    if not task.strip():
        return {}
    try:
        spec = json.loads(task)
    except json.JSONDecodeError as exc:
        raise SemanticError(
            type="parse_error",
            message=f"code_executor task must be JSON: {exc}",
        ) from exc
    if not isinstance(spec, dict):
        raise SemanticError(
            type="parse_error",
            message="code_executor task must be a JSON object",
        )
    return spec


async def _load_candidate_brief(
    context: list[dict[str, Any]],
) -> list[CandidatePattern]:
    """Fallback: pull candidates from an upstream ``candidate_brief`` artifact.

    The researcher node may embed its candidates inline in the work brief,
    in which case the planner passes them via ``task``. When task is empty
    we look across upstream artifacts for a JSON blob shaped like
    :class:`CandidateBrief`.
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
            if not isinstance(payload, dict) or "candidates" not in payload:
                continue
            try:
                brief = CandidateBrief.model_validate(payload)
            except Exception:
                continue
            return list(brief.candidates)
    return []


@code_executor(command="run")
async def code_executor_run(
    task: str, context: list[dict[str, Any]] | None = None
) -> str:
    """Run a single candidate against a fixture + assertions.

    Planner contract: ``task`` is a JSON string with keys ``candidate``
    (``{id, source_code, entrypoint}``), ``fixture`` (opaque dict given
    to the candidate at ``task.json``), ``assertions`` (list of
    declarative checks), and ``timeout_s`` (float seconds). Writes a
    ``candidate_report`` artifact with the :class:`ExecutionReport`.
    """
    emit_progress({"status": "running", "agent": "code_executor", "command": "run"})
    spec = _parse_task(task)
    candidate = spec.get("candidate") or {}
    if not candidate:
        raise SemanticError(
            type="parse_error",
            message="code_executor(run) requires 'candidate' in task",
        )
    pattern = CandidatePattern.model_validate(candidate)
    report = await run_candidate_in_subprocess(
        candidate_id=pattern.id,
        source_code=pattern.source_code,
        entrypoint=pattern.entrypoint,
        fixture=spec.get("fixture") or {},
        assertions=spec.get("assertions") or [],
        timeout_s=float(spec.get("timeout_s") or 30.0),
    )
    await get_artifacts().write(
        json.dumps(report.model_dump()).encode("utf-8"),
        content_type="application/json",
        summary=f"candidate_report:{pattern.id}",
        confidence=1.0,
        completeness="complete",
        tags={"candidate_report": True, "candidate_id": pattern.id},
        key="candidate_report",
    )
    if not report.ok:
        emit_signal(
            Signal(
                type=SignalType.PARTIAL_RESULT,
                reason=f"candidate {pattern.id!r} failed: exit={report.exit_code}",
                metadata={"candidate_id": pattern.id, "exit_code": report.exit_code},
            )
        )
    return json.dumps(report.model_dump())


@code_executor(command="eval_all")
async def code_executor_eval_all(
    task: str, context: list[dict[str, Any]] | None = None
) -> str:
    """Run every candidate in a list against a shared fixture + assertions.

    Planner contract: ``task`` is a JSON string with keys ``candidates``
    (optional — falls back to the upstream ``candidate_brief``),
    ``fixture``, ``assertions``, ``timeout_s`` (default 30), and
    ``max_parallel`` (default 4). Writes one ``trial_scorecard`` artifact
    that aggregates per-candidate :class:`ExecutionReport` entries.
    """
    emit_progress(
        {"status": "running", "agent": "code_executor", "command": "eval_all"}
    )
    spec = _parse_task(task)
    raw_candidates = spec.get("candidates")
    patterns: list[CandidatePattern]
    if isinstance(raw_candidates, list) and raw_candidates:
        patterns = [CandidatePattern.model_validate(c) for c in raw_candidates]
    else:
        patterns = await _load_candidate_brief(context or [])
    if not patterns:
        raise SemanticError(
            type="missing_input",
            message="code_executor(eval_all) received no candidates",
        )

    fixture = spec.get("fixture") or {}
    assertions = spec.get("assertions") or []
    timeout_s = float(spec.get("timeout_s") or 30.0)
    max_parallel = int(spec.get("max_parallel") or 4)

    semaphore = asyncio.Semaphore(max(1, max_parallel))

    async def _one(pattern: CandidatePattern) -> ExecutionReport:
        async with semaphore:
            return await run_candidate_in_subprocess(
                candidate_id=pattern.id,
                source_code=pattern.source_code,
                entrypoint=pattern.entrypoint,
                fixture=fixture,
                assertions=assertions,
                timeout_s=timeout_s,
            )

    reports = await asyncio.gather(*(_one(p) for p in patterns))
    if not any(r.ok for r in reports):
        emit_signal(
            Signal(
                type=SignalType.PARTIAL_RESULT,
                reason="No candidate satisfied all assertions",
                metadata={"candidate_count": len(reports)},
            )
        )
    elif any(not r.ok for r in reports):
        failed = sum(1 for r in reports if not r.ok)
        emit_signal(
            Signal(
                type=SignalType.PARTIAL_RESULT,
                reason=f"{failed}/{len(reports)} candidates failed",
                metadata={
                    "failed_ids": [r.candidate_id for r in reports if not r.ok],
                },
            )
        )

    scorecard = TrialScorecard(reports=reports)
    await get_artifacts().write(
        json.dumps(scorecard.model_dump()).encode("utf-8"),
        content_type="application/json",
        summary=f"trial_scorecard: {len(reports)} candidates",
        confidence=1.0,
        completeness="complete",
        tags={"trial_scorecard": True},
        key="trial_scorecard",
    )
    return json.dumps(scorecard.model_dump())
