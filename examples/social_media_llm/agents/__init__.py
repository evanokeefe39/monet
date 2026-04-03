"""Thin @agent wrappers for LLM-backed agents.

Each wrapper handles the monet SDK envelope (emit_progress, write_artifact,
emit_signal, exceptions) while delegating actual LLM logic to per-agent
modules that have zero monet imports.

SDK helper coverage:
  planner/fast    — emit_progress, get_run_logger
  planner/plan    — emit_progress, write_artifact, NeedsHumanReview
  researcher/deep — emit_progress, write_artifact, get_run_context
  writer/deep     — emit_progress, write_artifact
  qa/fast         — emit_progress, emit_signal, SemanticError
  publisher/pub   — emit_progress, get_run_logger, EscalationRequired
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
    get_run_context,
    get_run_logger,
    handle_agent_event,
    write_artifact,
)
from monet.exceptions import EscalationRequired, NeedsHumanReview, SemanticError

from .planner import run_planner_plan, run_planner_triage
from .publisher import run_publisher
from .qa import run_qa
from .researcher import run_researcher
from .writer import run_writer

# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


@agent(agent_id="sm-planner", command="fast")
async def planner_triage(task: str) -> str:
    """Classify incoming message complexity for routing."""
    log = get_run_logger()
    emit_progress({"status": "starting", "agent": "planner/fast"})
    log.info("Triaging request: %s", task[:80])

    result = await run_planner_triage(task)

    emit_progress({"status": "complete", "agent": "planner/fast"})
    log.info("Triage complete")
    return result


@agent(agent_id="sm-planner", command="plan")
async def planner_plan(task: str, context: list[Any] | None = None) -> str:
    """Build a structured work brief for content generation."""
    emit_progress({"status": "starting", "agent": "planner/plan"})

    # Extract human feedback from context if present
    feedback = None
    if context:
        for entry in context:
            entry_type = getattr(entry, "type", None) or (
                entry.get("type") if isinstance(entry, dict) else None
            )
            if entry_type == "instruction":
                feedback = getattr(entry, "content", None) or (
                    entry.get("content") if isinstance(entry, dict) else None
                )
                break

    brief = await run_planner_plan(task, feedback=feedback)

    # Write pretty-printed brief to catalogue for reference
    brief_pretty = json.dumps(brief, indent=2)
    await write_artifact(
        content=brief_pretty.encode(),
        content_type="application/json",
        summary=str(brief.get("goal", task[:100])),
        confidence=0.85,
        completeness="complete",
    )

    # Check for sensitive topics — raise HITL if flagged
    if brief.get("is_sensitive"):
        raise NeedsHumanReview(
            reason=f"Topic may be sensitive: {brief.get('goal', task)}"
        )

    emit_progress({"status": "complete", "agent": "planner/plan"})
    # Return compact JSON to stay under content limit (4000 bytes)
    # so the decorator doesn't auto-offload the inline output.
    # The graph needs this as a parseable JSON string.
    return json.dumps(brief, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Researcher
# ---------------------------------------------------------------------------


@agent(agent_id="sm-researcher", command="deep")
async def researcher_deep(task: str) -> str:
    """Exhaustive web research using search tools and LLM synthesis."""
    ctx = get_run_context()
    emit_progress({"status": "starting", "agent": "researcher/deep"})

    result = await run_researcher(task, trace_id=ctx.trace_id)

    # Write full research to catalogue
    await write_artifact(
        content=result.encode(),
        content_type="text/markdown",
        summary=result[:200],
        confidence=0.85,
        completeness="complete",
    )

    emit_progress({"status": "complete", "agent": "researcher/deep"})
    return result


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


@agent(agent_id="sm-writer", command="deep")
async def writer_deep(task: str) -> str:
    """Generate platform-specific social media content."""
    emit_progress({"status": "starting", "agent": "writer/deep"})

    content = await run_writer(task)

    # Write content to catalogue
    await write_artifact(
        content=content.encode(),
        content_type="text/plain",
        summary=content[:200],
        confidence=0.8,
        completeness="complete",
    )

    emit_progress({"status": "complete", "agent": "writer/deep"})
    return content


# ---------------------------------------------------------------------------
# QA
# ---------------------------------------------------------------------------


@agent(agent_id="sm-qa", command="fast")
async def qa_fast(task: str) -> str:
    """Evaluate content quality against work brief criteria."""
    emit_progress({"status": "evaluating", "agent": "qa/fast"})

    verdict = await run_qa(task)
    confidence = float(verdict.get("confidence", 0.7))

    if confidence < 0.5:
        # Fatal — content quality too low to be useful
        raise SemanticError(
            type="quality",
            message=f"Content quality below threshold: {verdict.get('notes', '')}",
        )

    if confidence < 0.7:
        # Non-fatal — return verdict alongside signal
        emit_signal(
            Signal(
                type=SignalType.NEEDS_HUMAN_REVIEW,
                reason=(
                    f"Marginal quality (confidence {confidence:.2f}): "
                    f"{verdict.get('notes', '')}"
                ),
                metadata={"confidence": confidence},
            )
        )

    emit_progress(
        {
            "status": "complete",
            "agent": "qa/fast",
            "verdict": verdict.get("verdict"),
        }
    )
    return json.dumps(verdict)


# ---------------------------------------------------------------------------
# Publisher (CLI agent)
# ---------------------------------------------------------------------------


@agent(agent_id="sm-publisher", command="publish")
async def publisher_publish(task: str) -> str:
    """Format and publish content via CLI formatting tool."""
    log = get_run_logger()
    ctx = get_run_context()
    emit_progress({"status": "publishing", "agent": "publisher/publish"})
    log.info("Publisher invoked for run_id=%s", ctx.run_id)

    try:
        events = await run_publisher(task)
    except RuntimeError as exc:
        raise EscalationRequired(reason=f"Publisher CLI failed: {exc}") from exc

    # Route events through handle_agent_event
    result_output = None
    for event in events:
        output = await handle_agent_event(event)
        if output is not None:
            result_output = output

    emit_progress({"status": "complete", "agent": "publisher/publish"})
    log.info("Publisher complete")
    return result_output or ""
