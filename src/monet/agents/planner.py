"""Reference planner agent — triage and work brief generation."""

from __future__ import annotations

import functools
import json
import os
from typing import Any

from monet import agent, emit_progress, get_run_logger
from monet.exceptions import NeedsHumanReview

_TRIAGE_PROMPT = """You are a request triage agent. Classify the user's request.

Return ONLY valid JSON:
{
  "complexity": "simple" | "bounded" | "complex",
  "suggested_agents": ["agent_id", ...],
  "requires_planning": true | false
}

simple: trivial response, no agents needed
bounded: 1-2 agents, single shot
complex: multi-step plan with multiple agents and waves

Request: {task}
"""

_PLAN_PROMPT = """You are a planning agent. Produce a structured work brief.

Return ONLY valid JSON with this shape:
{
  "goal": "...",
  "in_scope": [...],
  "out_of_scope": [...],
  "is_sensitive": true | false,
  "phases": [{"name": "...", "waves": [{"items": [
    {"agent_id": "...", "command": "...", "task": "..."}
  ]}]}],
  "assumptions": [...]
}

Available agents: researcher, writer, qa, publisher.

Task: {task}
{feedback_section}
"""


@functools.cache
def _get_model(model_string: str) -> Any:
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string)


def _model_string() -> str:
    return os.environ.get("MONET_PLANNER_MODEL", "google_genai:gemini-2.5-flash")


@agent(agent_id="planner", command="fast")
async def planner_fast(task: str) -> str:
    """Classify request complexity. Returns JSON triage."""
    log = get_run_logger()
    emit_progress({"status": "triaging", "agent": "planner"})
    log.info("planner/fast triaging: %s", task[:80])

    model = _get_model(_model_string())
    prompt = _TRIAGE_PROMPT.replace("{task}", task)
    response = await model.ainvoke([{"role": "user", "content": prompt}])

    content = response.content if hasattr(response, "content") else str(response)
    if isinstance(content, list):
        content = "".join(str(p) for p in content)
    return _strip_json_fence(content)


@agent(agent_id="planner", command="plan")
async def planner_plan(task: str, context: list[dict[str, Any]] | None = None) -> str:
    """Build a structured work brief. Returns JSON."""
    emit_progress({"status": "planning", "agent": "planner"})

    feedback = ""
    if context:
        for entry in context:
            if entry.get("type") == "instruction":
                feedback = entry.get("content", "")
                break

    feedback_section = (
        f"\nHuman feedback to incorporate: {feedback}" if feedback else ""
    )
    prompt = _PLAN_PROMPT.replace("{task}", task).replace(
        "{feedback_section}", feedback_section
    )

    model = _get_model(_model_string())
    response = await model.ainvoke([{"role": "user", "content": prompt}])
    content = response.content if hasattr(response, "content") else str(response)
    if isinstance(content, list):
        content = "".join(str(p) for p in content)

    raw = _strip_json_fence(content)
    try:
        brief = json.loads(raw)
    except json.JSONDecodeError:
        brief = {"goal": task, "phases": [], "assumptions": []}

    if brief.get("is_sensitive"):
        goal = brief.get("goal", task)
        raise NeedsHumanReview(reason=f"Topic may be sensitive: {goal}")

    return json.dumps(brief, separators=(",", ":"))


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    return text.strip()
