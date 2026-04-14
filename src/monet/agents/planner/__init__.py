"""Reference planner agent — triage and work brief generation."""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from monet import (
    agent,
    emit_progress,
    get_agent_manifest,
    get_run_logger,
    write_artifact,
)
from monet.exceptions import NeedsHumanReview
from monet.orchestration._state import WorkBrief

from .._prompts import extract_text, make_env

_env = make_env(Path(__file__).parent)


@functools.cache
def _get_model(model_string: str, *, temperature: float = 0.0) -> Any:
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string, temperature=temperature)


def _model_string() -> str:
    return os.environ.get("MONET_PLANNER_MODEL", "google_genai:gemini-2.5-flash")


_PLANNER_EXCLUDE: tuple[str, ...] = ("planner",)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    return text.strip()


def _build_roster() -> list[dict[str, Any]]:
    """Return all declared agents sorted by (agent_id, command).

    Planner self-exclusion is applied at prompt-render time by filtering
    on ``agent_id``. Keeping the manifest read unfiltered means the
    manifest stays a dumb service — business logic lives in the planner.
    """
    return sorted(
        (dict(cap) for cap in get_agent_manifest().list_agents()),
        key=lambda c: (c["agent_id"], c["command"]),
    )


planner = agent("planner")


@planner(command="fast")
async def planner_fast(task: str, context: list[dict[str, Any]] | None = None) -> str:
    """Classify request complexity. Returns JSON triage."""
    log = get_run_logger()
    emit_progress({"status": "triaging", "agent": "planner"})
    log.info("planner/fast triaging: %s", task[:80])

    roster = [cap for cap in _build_roster() if cap["agent_id"] not in _PLANNER_EXCLUDE]
    prompt = _env.get_template("triage.j2").render(
        task=task,
        context=context or [],
        roster=roster,
    )
    response = await _get_model(_model_string()).ainvoke(
        [{"role": "user", "content": prompt}]
    )
    return _strip_json_fence(extract_text(response))


@planner(command="plan")
async def planner_plan(
    task: str, context: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Build a structured work brief with flat routing DAG.

    Returns inline output with ``work_brief_artifact_id`` (keyed artifact
    in the catalogue) and ``routing_skeleton`` (flat DAG for the
    execution graph). The orchestrator never reads the full brief —
    workers resolve it via the inject_plan_context hook at invocation
    time.
    """
    emit_progress({"status": "planning", "agent": "planner"})

    feedback = ""
    for entry in context or []:
        if entry.get("type") == "instruction":
            feedback = entry.get("content", "")
            break

    roster = [cap for cap in _build_roster() if cap["agent_id"] not in _PLANNER_EXCLUDE]
    prompt = _env.get_template("plan.j2").render(
        task=task,
        context=context or [],
        feedback=feedback,
        roster=roster,
    )
    response = await _get_model(_model_string()).ainvoke(
        [{"role": "user", "content": prompt}]
    )
    raw = _strip_json_fence(extract_text(response))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Planner returned non-JSON output: {exc}"
        raise ValueError(msg) from exc

    # Validate shape before writing anything. WorkBrief validation guarantees
    # to_routing_skeleton() succeeds without its own validation pass.
    try:
        work_brief = WorkBrief.model_validate(payload)
    except ValidationError as exc:
        msg = f"Planner output failed WorkBrief validation: {exc}"
        raise ValueError(msg) from exc

    if work_brief.is_sensitive:
        raise NeedsHumanReview(reason=f"Topic may be sensitive: {work_brief.goal}")

    routing_skeleton = work_brief.to_routing_skeleton()

    pointer = await write_artifact(
        content=work_brief.model_dump_json().encode(),
        content_type="application/json",
        summary=f"Work brief: {work_brief.goal[:100]}",
        confidence=1.0,
        completeness="complete",
        key="work_brief",
    )

    return {
        "work_brief_artifact_id": pointer["artifact_id"],
        "routing_skeleton": routing_skeleton.model_dump(),
    }
