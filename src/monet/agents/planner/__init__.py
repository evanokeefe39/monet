"""Reference planner agent — triage and work brief generation."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from monet import (
    Signal,
    SignalType,
    agent,
    emit_progress,
    emit_signal,
    get_run_logger,
    write_artifact,
)
from monet.config._env import agent_model
from monet.core.registry import default_registry
from monet.orchestration.prebuilt._state import WorkBrief

from .._prompts import extract_text, make_env

#: Max number of follow-up questions the planner may ask in one turn.
#: Keeps the form manageable for the human and discourages "ten shallow
#: questions" over "three pointed ones". Enforced in both prompt and
#: post-parse validation.
MAX_FOLLOWUP_QUESTIONS = 5

_env = make_env(Path(__file__).parent)


class TriageResult(BaseModel):
    """Structured triage output. Forces the model to commit to a Literal."""

    complexity: Literal["simple", "bounded", "complex"]
    suggested_agents: list[str] = Field(default_factory=list)
    requires_planning: bool


@functools.cache
def _get_model(model_string: str, *, temperature: float = 0.0) -> Any:
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string, temperature=temperature)


def _model_string() -> str:
    return agent_model("planner", "groq:llama-3.3-70b-versatile")


_PLANNER_EXCLUDE: tuple[str, ...] = ("planner",)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    return text.strip()


def _build_roster(context: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return the fleet-wide agent roster for planner prompt rendering.

    Preference order:

    1. ``agent_roster`` entry in *context* — the orchestrator (running in
       the server process) injects the authoritative fleet view from
       :class:`~monet.server._capabilities.CapabilityIndex` so the planner
       can compose across pools (S2/S3 split-fleet).
    2. Worker's ``default_registry`` — fallback for unit tests and any
       caller that invokes the planner without orchestration.

    Planner self-exclusion is applied at prompt-render time by the caller.
    """
    for entry in context or []:
        if entry.get("type") == "agent_roster":
            agents = entry.get("agents") or []
            return sorted(
                (dict(a) for a in agents),
                key=lambda c: (c.get("agent_id", ""), c.get("command", "")),
            )
    roster = default_registry.registered_agents(with_docstrings=True)
    return sorted(
        (
            {
                "agent_id": row.agent_id,
                "command": row.command,
                "description": row.description,
            }
            for row in roster
        ),
        key=lambda c: (c["agent_id"], c["command"]),
    )


planner = agent("planner")


@planner(command="fast")
async def planner_fast(task: str, context: list[dict[str, Any]] | None = None) -> str:
    """Classify request complexity. Returns JSON triage.

    Uses ``with_structured_output(TriageResult)`` so the model must commit
    to a ``Literal`` complexity value instead of drifting into freeform
    text that falls back to the parse-failure default. Falls back to
    raw-text parse when the model backend does not return a pydantic
    instance (primarily in tests where ``_get_model`` is mocked).
    """
    log = get_run_logger()
    model_short = _model_string().split(":")[-1]
    emit_progress({"status": f"thinking[{model_short}]...", "agent": "planner"})
    log.info("planner/fast triaging: %s", task[:80])

    roster = [
        cap for cap in _build_roster(context) if cap["agent_id"] not in _PLANNER_EXCLUDE
    ]
    prompt = _env.get_template("triage.j2").render(
        task=task,
        context=context or [],
        roster=roster,
    )
    base_model = _get_model(_model_string())
    structured = base_model.with_structured_output(TriageResult)
    result = await structured.ainvoke([{"role": "user", "content": prompt}])
    if isinstance(result, TriageResult):
        return json.dumps(result.model_dump())
    # Mock / non-structured fallback: treat as raw chat output.
    return _strip_json_fence(extract_text(result))


@planner(command="plan")
async def planner_plan(
    task: str, context: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Build a work brief **or** ask follow-up questions when ambiguous.

    Output is one of two shapes, discriminated by the ``kind`` field:

    - ``{"kind": "plan", "work_brief_artifact_id", "routing_skeleton"}``
      — the happy path. Work brief written to the artifact store, flat
      routing DAG inlined for the execution graph.
    - ``{"kind": "questions", "questions": [...]}`` — the planner can't
      plan without more info. Emits a ``NEEDS_CLARIFICATION`` signal so
      the orchestrator knows to pause and collect answers. The follow-up
      loop is bounded elsewhere (chat graph + pipelines); the agent's
      sole job is to emit accurate questions, not to manage attempts.

    Orchestrators resolve the full brief via ``inject_plan_context`` at
    invocation time; neither output dict is read for content beyond the
    discriminator + pointer.
    """
    model_short = _model_string().split(":")[-1]
    emit_progress({"status": f"thinking[{model_short}]...", "agent": "planner"})

    feedback = ""
    clarification_answers: list[dict[str, Any]] = []
    for entry in context or []:
        if entry.get("type") == "instruction":
            feedback = entry.get("content", "")
        elif entry.get("type") == "user_clarification":
            clarification_answers.append(entry)

    roster = [
        cap for cap in _build_roster(context) if cap["agent_id"] not in _PLANNER_EXCLUDE
    ]
    prompt = _env.get_template("plan.j2").render(
        task=task,
        context=context or [],
        feedback=feedback,
        clarification_answers=clarification_answers,
        roster=roster,
        max_followup_questions=MAX_FOLLOWUP_QUESTIONS,
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

    if not isinstance(payload, dict):
        msg = f"Planner output must be a JSON object, got {type(payload).__name__}"
        raise ValueError(msg)

    kind = payload.get("kind")
    if kind == "questions":
        return _emit_questions(payload)
    # Legacy outputs without a ``kind`` field are treated as plans for
    # backwards compatibility with any consumer that still posts the flat
    # WorkBrief shape directly.
    return await _emit_plan(payload)


def _emit_questions(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate + emit a clarification output.

    Also fires a :data:`SignalType.NEEDS_CLARIFICATION` signal so the
    orchestrator can route on the signal group rather than on dict-shape
    inspection. Over-limit question lists are truncated — better to ask
    five good questions than reject the whole turn.
    """
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        msg = "Planner 'questions' output must include a non-empty questions list"
        raise ValueError(msg)
    questions = [str(q).strip() for q in raw_questions if str(q).strip()]
    if not questions:
        msg = "Planner questions list had no usable entries after trimming"
        raise ValueError(msg)
    if len(questions) > MAX_FOLLOWUP_QUESTIONS:
        questions = questions[:MAX_FOLLOWUP_QUESTIONS]
    reason = str(payload.get("reasoning") or "Planner needs more information")
    emit_signal(
        Signal(
            type=SignalType.NEEDS_CLARIFICATION,
            reason=reason,
            metadata={"question_count": len(questions)},
        )
    )
    return {"kind": "questions", "questions": questions, "reasoning": reason}


async def _emit_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a WorkBrief payload, persist it, return the dispatch shape."""
    # Strip discriminator before WorkBrief validation — pydantic would
    # reject it otherwise since WorkBrief has no ``kind`` field.
    work_brief_payload = {k: v for k, v in payload.items() if k != "kind"}
    try:
        work_brief = WorkBrief.model_validate(work_brief_payload)
    except ValidationError as exc:
        msg = f"Planner output failed WorkBrief validation: {exc}"
        raise ValueError(msg) from exc

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
        "kind": "plan",
        "work_brief_artifact_id": pointer["artifact_id"],
        "routing_skeleton": routing_skeleton.model_dump(),
    }
