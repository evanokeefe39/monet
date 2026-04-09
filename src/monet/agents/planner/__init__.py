"""Reference planner agent — triage and work brief generation."""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

from monet import agent, emit_progress, get_run_logger
from monet._manifest import default_manifest
from monet.exceptions import NeedsHumanReview

from .._prompts import extract_text, make_env

_env = make_env(Path(__file__).parent)


@functools.cache
def _get_model(model_string: str) -> Any:
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string)


def _model_string() -> str:
    return os.environ.get("MONET_PLANNER_MODEL", "google_genai:gemini-2.5-flash")


_PLANNER_EXCLUDE: tuple[str, ...] = ("planner",)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    return text.strip()


planner = agent("planner")


@planner(command="fast")
async def planner_fast(task: str, context: list[dict[str, Any]] | None = None) -> str:
    """Classify request complexity. Returns JSON triage."""
    log = get_run_logger()
    emit_progress({"status": "triaging", "agent": "planner"})
    log.info("planner/fast triaging: %s", task[:80])

    roster = sorted(
        (
            cap
            for cap in default_manifest.capabilities()
            if cap["agent_id"] not in _PLANNER_EXCLUDE
        ),
        key=lambda c: (c["agent_id"], c["command"]),
    )
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
async def planner_plan(task: str, context: list[dict[str, Any]] | None = None) -> str:
    """Build a structured work brief. Returns JSON."""
    emit_progress({"status": "planning", "agent": "planner"})

    feedback = ""
    for entry in context or []:
        if entry.get("type") == "instruction":
            feedback = entry.get("content", "")
            break

    roster = sorted(
        (
            cap
            for cap in default_manifest.capabilities()
            if cap["agent_id"] not in _PLANNER_EXCLUDE
        ),
        key=lambda c: (c["agent_id"], c["command"]),
    )
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
        brief = json.loads(raw)
    except json.JSONDecodeError:
        brief = {"goal": task, "phases": [], "assumptions": []}

    if brief.get("is_sensitive"):
        goal = brief.get("goal", task)
        raise NeedsHumanReview(reason=f"Topic may be sensitive: {goal}")

    return json.dumps(brief, separators=(",", ":"))
