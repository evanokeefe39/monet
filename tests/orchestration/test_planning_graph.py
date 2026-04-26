# mypy: disable-error-code="call-overload,arg-type"
"""Tests for the parameterised planning subgraph.

Covers the planner invocation, questionnaire loop (when
``max_followup_attempts > 0``), and approval state machine. The pipeline
default (``max_followup_attempts=0``) is covered in
``test_default_compound_graph.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import MemorySaver

from monet.orchestration.prebuilt.planning_graph import (
    MAX_REVISIONS,
    build_planning_subgraph,
    human_approval_node,
    planner_node,
    questionnaire_node,
    route_from_approval,
    route_from_planner,
)
from monet.signals import SignalType


def _result(
    output: Any = None,
    signals: list[dict[str, Any]] | None = None,
    artifacts: tuple[dict[str, Any], ...] = (),
    success: bool = True,
) -> MagicMock:
    r = MagicMock()
    r.success = success
    r.output = output
    r.signals = signals or []
    r.artifacts = artifacts
    return r


def _plan_result(goal: str = "Do it", artifact_id: str = "brief-1") -> MagicMock:
    skeleton = {
        "goal": goal,
        "nodes": [
            {"id": "n1", "agent_id": "researcher", "command": "fast", "depends_on": []}
        ],
    }
    return _result(
        output={
            "kind": "plan",
            "goal": goal,
            "work_brief_artifact_id": artifact_id,
            "routing_skeleton": skeleton,
        },
        artifacts=(
            {
                "artifact_id": artifact_id,
                "url": f"/v1/{artifact_id}",
                "key": "work_brief",
            },
        ),
    )


# --- planner_node --------------------------------------------------------


async def test_planner_node_writes_pointer_and_skeleton_on_plan() -> None:
    with patch(
        "monet.orchestration.prebuilt.planning_graph.invoke_agent",
        return_value=_plan_result(goal="p"),
    ):
        out = await planner_node({"task": "plan a thing"}, {})  # type: ignore[arg-type]
    assert out["work_brief_pointer"]["artifact_id"] == "brief-1"
    assert out["routing_skeleton"]["goal"] == "p"
    assert out["planner_error"] is None
    assert out["pending_questions"] is None


async def test_planner_node_failure_writes_error_pipeline_mode() -> None:
    """Pipeline default (max_followup_attempts=0): questions = failure."""
    with patch(
        "monet.orchestration.prebuilt.planning_graph.invoke_agent",
        return_value=_result(
            output={"kind": "questions", "questions": ["scope?"]},
            signals=[
                {
                    "type": SignalType.NEEDS_CLARIFICATION,
                    "reason": "x",
                    "metadata": None,
                }
            ],
        ),
    ):
        out = await planner_node({"task": "x"}, {})  # type: ignore[arg-type]
    # Legacy module-level planner_node wraps _invoke_planner with
    # force_plan=False — questions path still populates pending_questions
    # but the default router treats missing pointer as planning_failed.
    assert out["pending_questions"] == ["scope?"]
    assert out["work_brief_pointer"] is None


async def test_planner_node_invoke_failure_surfaces_reason() -> None:
    with patch(
        "monet.orchestration.prebuilt.planning_graph.invoke_agent",
        return_value=_result(
            success=False,
            signals=[{"type": "FAILURE", "reason": "boom", "metadata": None}],
        ),
    ):
        out = await planner_node({"task": "x"}, {})  # type: ignore[arg-type]
    assert out["planner_error"] and "boom" in out["planner_error"]
    assert out["work_brief_pointer"] is None


# --- route_from_planner (default variant) --------------------------------


def test_route_from_planner_pointer_routes_approval() -> None:
    assert (
        route_from_planner(  # type: ignore[arg-type]
            {"work_brief_pointer": {"artifact_id": "x", "url": "/y"}}
        )
        == "human_approval"
    )


def test_route_from_planner_no_pointer_routes_failed() -> None:
    assert route_from_planner({}) == "planning_failed"  # type: ignore[arg-type]


# --- human_approval_node -------------------------------------------------


async def test_approval_approve_writes_plan_approved() -> None:
    state: dict[str, Any] = {
        "work_brief_pointer": {"artifact_id": "b", "url": "/b"},
        "routing_skeleton": {"goal": "g", "nodes": []},
    }
    with patch(
        "monet.orchestration.prebuilt.planning_graph.interrupt",
        return_value={"action": "approve"},
    ):
        out = await human_approval_node(state)  # type: ignore[arg-type]
    assert out["plan_approved"] is True
    assert out["messages"] == [{"role": "user", "content": "action=approve"}]


async def test_approval_form_prompt_renders_plan_summary() -> None:
    """Approval interrupt carries the plan summary so the TUI shows it."""
    captured: dict[str, Any] = {}

    def capture(payload: Any) -> Any:
        captured["payload"] = payload
        return {"action": "approve"}

    state: dict[str, Any] = {
        "work_brief_pointer": {"artifact_id": "brief-z", "url": "/b"},
        "routing_skeleton": {
            "goal": "Research AI trends in healthcare",
            "nodes": [
                {
                    "id": "research",
                    "agent_id": "researcher",
                    "command": "deep",
                    "depends_on": [],
                },
                {
                    "id": "write",
                    "agent_id": "writer",
                    "command": "deep",
                    "depends_on": ["research"],
                },
            ],
        },
    }
    with patch(
        "monet.orchestration.prebuilt.planning_graph.interrupt", side_effect=capture
    ):
        await human_approval_node(state)  # type: ignore[arg-type]
    prompt = captured["payload"]["prompt"]
    assert "Research AI trends in healthcare" in prompt
    assert "2 steps" in prompt
    assert "research: researcher/deep" in prompt
    assert "write: writer/deep ← research" in prompt
    assert "brief-z" in prompt
    assert "Approve this plan?" in prompt


async def test_approval_reject_writes_plan_approved_false() -> None:
    state: dict[str, Any] = {
        "work_brief_pointer": {"artifact_id": "b", "url": "/b"},
    }
    with patch(
        "monet.orchestration.prebuilt.planning_graph.interrupt",
        return_value={"action": "reject"},
    ):
        out = await human_approval_node(state)  # type: ignore[arg-type]
    assert out["plan_approved"] is False
    assert out["messages"] == [{"role": "user", "content": "action=reject"}]


async def test_approval_revise_with_feedback_under_budget() -> None:
    state: dict[str, Any] = {
        "work_brief_pointer": {"artifact_id": "b", "url": "/b"},
        "revision_count": 0,
    }
    with patch(
        "monet.orchestration.prebuilt.planning_graph.interrupt",
        return_value={"action": "revise", "feedback": "tighter scope"},
    ):
        out = await human_approval_node(state)  # type: ignore[arg-type]
    assert out["plan_approved"] is False
    assert out["human_feedback"] == "tighter scope"
    assert out["revision_count"] == 1


async def test_approval_revise_at_max_budget_falls_through_to_false() -> None:
    state: dict[str, Any] = {
        "work_brief_pointer": {"artifact_id": "b", "url": "/b"},
        "revision_count": MAX_REVISIONS,
    }
    with patch(
        "monet.orchestration.prebuilt.planning_graph.interrupt",
        return_value={"action": "revise", "feedback": "again"},
    ):
        out = await human_approval_node(state)  # type: ignore[arg-type]
    assert out["plan_approved"] is False
    assert out["messages"] == [{"role": "user", "content": "action=revise"}]


def test_route_from_approval_revise_loops_back_under_budget() -> None:
    state: dict[str, Any] = {"human_feedback": "fix", "revision_count": 0}
    assert route_from_approval(state) == "planner"  # type: ignore[arg-type]


def test_route_from_approval_approved_ends() -> None:
    from langgraph.graph import END

    assert route_from_approval({"plan_approved": True}) == END  # type: ignore[arg-type]


# --- questionnaire_node --------------------------------------------------


async def test_questionnaire_interrupts_and_bumps_attempts() -> None:
    with patch(
        "monet.orchestration.prebuilt.planning_graph.interrupt",
        return_value={"q0": "small scope", "q1": "__skip__"},
    ):
        out = await questionnaire_node(
            {  # type: ignore[arg-type]
                "pending_questions": ["scope?", "deadline?"],
                "followup_attempts": 0,
            }
        )
    assert out["pending_questions"] is None
    assert out["followup_attempts"] == 1
    answers = out["followup_answers"]
    assert len(answers) == 1  # skipped question filtered out
    assert answers[0]["summary"] == "scope?"
    assert "small scope" in answers[0]["content"]


async def test_questionnaire_no_pending_is_no_op() -> None:
    out = await questionnaire_node({"followup_attempts": 2})  # type: ignore[arg-type]
    assert out == {"pending_questions": None, "followup_attempts": 2}


# --- parameterised subgraph ---------------------------------------------


async def test_subgraph_with_questionnaire_round_then_plan() -> None:
    """max_followup_attempts=1: first call asks, resume plans, then approve."""
    calls = {"count": 0}

    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        calls["count"] += 1
        if calls["count"] == 1:
            return _result(
                output={"kind": "questions", "questions": ["scope?"]},
                signals=[
                    {
                        "type": SignalType.NEEDS_CLARIFICATION,
                        "reason": "x",
                        "metadata": None,
                    }
                ],
            )
        return _plan_result(goal="planned")

    interrupts = [
        {"q0": "AI"},  # questionnaire answer
        {"action": "approve"},  # approval
    ]

    def fake_interrupt(_payload: Any) -> Any:
        return interrupts.pop(0)

    with (
        patch(
            "monet.orchestration.prebuilt.planning_graph.invoke_agent",
            side_effect=fake_invoke,
        ),
        patch(
            "monet.orchestration.prebuilt.planning_graph.interrupt",
            side_effect=fake_interrupt,
        ),
    ):
        graph = build_planning_subgraph(max_followup_attempts=1).compile(
            checkpointer=MemorySaver()
        )
        out = await graph.ainvoke(
            {"task": "do a thing"},
            config={"configurable": {"thread_id": "pg-1"}},
        )
    assert calls["count"] == 2
    assert out["plan_approved"] is True
    assert out["work_brief_pointer"]["artifact_id"] == "brief-1"


async def test_subgraph_pipeline_mode_fails_on_questions() -> None:
    """max_followup_attempts=0: questions = failure, no interrupt."""
    with patch(
        "monet.orchestration.prebuilt.planning_graph.invoke_agent",
        return_value=_result(
            output={"kind": "questions", "questions": ["scope?"]},
            signals=[
                {
                    "type": SignalType.NEEDS_CLARIFICATION,
                    "reason": "x",
                    "metadata": None,
                }
            ],
        ),
    ):
        graph = build_planning_subgraph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"task": "do a thing"},
            config={"configurable": {"thread_id": "pg-2"}},
        )
    assert out.get("plan_approved") is False
    assert out.get("work_brief_pointer") is None


async def test_subgraph_force_plan_after_budget() -> None:
    """max_followup_attempts=1 + planner asks twice: second call is force-planned."""
    captured: list[list[dict[str, Any]]] = []

    async def fake_invoke(*_args: Any, **kwargs: Any) -> Any:
        captured.append(kwargs.get("context") or [])
        if len(captured) == 1:
            return _result(
                output={"kind": "questions", "questions": ["scope?"]},
                signals=[
                    {
                        "type": SignalType.NEEDS_CLARIFICATION,
                        "reason": "x",
                        "metadata": None,
                    }
                ],
            )
        return _plan_result(goal="forced")

    interrupts = [
        {"q0": "small"},  # first questionnaire
        {"action": "approve"},  # approval on the forced plan
    ]

    def fake_interrupt(_payload: Any) -> Any:
        return interrupts.pop(0)

    with (
        patch(
            "monet.orchestration.prebuilt.planning_graph.invoke_agent",
            side_effect=fake_invoke,
        ),
        patch(
            "monet.orchestration.prebuilt.planning_graph.interrupt",
            side_effect=fake_interrupt,
        ),
    ):
        graph = build_planning_subgraph(max_followup_attempts=1).compile(
            checkpointer=MemorySaver()
        )
        out = await graph.ainvoke(
            {"task": "do a thing"},
            config={"configurable": {"thread_id": "pg-3"}},
        )
    # Second invocation sees the force-plan instruction in context.
    assert any(
        isinstance(entry, dict) and entry.get("summary") == "Force-plan override"
        for entry in captured[1]
    )
    assert out["plan_approved"] is True
