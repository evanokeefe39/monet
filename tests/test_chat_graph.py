# mypy: disable-error-code="call-overload,arg-type"
"""Tests for the chat graph — parse, triage, respond, planner, specialist."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from monet.orchestration.chat_graph import (
    MAX_FOLLOWUP_ATTEMPTS,
    PLAN_MAX_REVISIONS,
    ChatState,
    ChatTriageResult,
    _build_context,
    _parse_slash,
    approval_node,
    build_chat_graph,
    parse_command_node,
    planner_node,
    questionnaire_node,
    respond_node,
    specialist_node,
    triage_node,
)
from monet.signals import SignalType

# --- parse_command_node ---------------------------------------------------


def test_parse_slash_no_slash_returns_none_route() -> None:
    assert _parse_slash("hello there") == {"route": None}


def test_parse_slash_plan_extracts_remainder() -> None:
    result = _parse_slash("/plan draft a roadmap")
    assert result == {"route": "planner", "command_meta": {"task": "draft a roadmap"}}


def test_parse_slash_specialist_parses_agent_and_mode() -> None:
    result = _parse_slash("/researcher:deep find TPS patterns")
    assert result == {
        "route": "specialist",
        "command_meta": {
            "specialist": "researcher",
            "mode": "deep",
            "task": "find TPS patterns",
        },
    }


def test_parse_slash_unknown_routes_chat_with_unknown_command() -> None:
    result = _parse_slash("/what")
    assert result == {"route": "chat", "command_meta": {"unknown_command": "/what"}}


async def test_parse_command_node_reads_last_user_message() -> None:
    state: ChatState = {
        "messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "/plan something"},
        ],
    }
    out = await parse_command_node(state)
    assert out["route"] == "planner"
    assert out["command_meta"]["task"] == "something"


# --- _build_context (no truncation) --------------------------------------


def test_build_context_excludes_last_message_and_does_not_truncate() -> None:
    big = "x" * 5000
    messages = [
        {"role": "user", "content": big},
        {"role": "assistant", "content": "short"},
        {"role": "user", "content": "task"},
    ]
    ctx = _build_context(messages)
    assert len(ctx) == 2
    assert ctx[0]["type"] == "chat_history"
    assert ctx[0]["content"] == big  # full length round-trip
    assert ctx[1]["role"] == "assistant"


# --- triage_node ---------------------------------------------------------


def _fake_triage_model(result: ChatTriageResult) -> MagicMock:
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=result)
    model = MagicMock()
    model.with_structured_output = MagicMock(return_value=structured)
    return model


async def test_triage_node_routes_chat() -> None:
    fake = _fake_triage_model(ChatTriageResult(route="chat", confidence=0.9))
    with patch("monet.orchestration.chat_graph._load_model", return_value=fake):
        out = await triage_node({"messages": [{"role": "user", "content": "hi"}]})
    assert out["route"] == "chat"


async def test_triage_node_routes_plan_to_planner_edge() -> None:
    """Triage returns route='plan'; the graph edge name is 'planner'."""
    fake = _fake_triage_model(ChatTriageResult(route="plan", confidence=0.9))
    with patch("monet.orchestration.chat_graph._load_model", return_value=fake):
        out = await triage_node(
            {"messages": [{"role": "user", "content": "plan a feature"}]}
        )
    assert out["route"] == "planner"
    assert out["command_meta"]["task"] == "plan a feature"


async def test_triage_node_clarification_routes_chat() -> None:
    fake = _fake_triage_model(
        ChatTriageResult(
            route="plan",
            confidence=0.3,
            clarification_needed=True,
            clarification_prompt="Be more specific about the scope.",
        )
    )
    with patch("monet.orchestration.chat_graph._load_model", return_value=fake):
        out = await triage_node(
            {"messages": [{"role": "user", "content": "do the thing"}]}
        )
    assert out["route"] == "chat"
    assert "clarification_prompt" in out["command_meta"]


async def test_triage_node_llm_exception_falls_back_to_chat() -> None:
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=RuntimeError("provider down"))
    model = MagicMock()
    model.with_structured_output = MagicMock(return_value=structured)
    with patch("monet.orchestration.chat_graph._load_model", return_value=model):
        out = await triage_node({"messages": [{"role": "user", "content": "hi"}]})
    assert out["route"] == "chat"


# --- respond_node --------------------------------------------------------


async def test_respond_node_calls_llm_directly_no_invoke_agent() -> None:
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=AIMessage(content="hello back"))
    with (
        patch("monet.orchestration.chat_graph._load_model", return_value=fake_model),
        patch("monet.orchestration.chat_graph.invoke_agent") as invoke_mock,
    ):
        out = await respond_node({"messages": [{"role": "user", "content": "hi"}]})
    assert invoke_mock.call_count == 0
    assert out["messages"][0]["content"] == "hello back"


async def test_respond_node_unknown_command_renders_inline_error_without_llm() -> None:
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock()
    with patch("monet.orchestration.chat_graph._load_model", return_value=fake_model):
        out = await respond_node(
            {
                "messages": [{"role": "user", "content": "/what"}],
                "command_meta": {"unknown_command": "/what"},
            }
        )
    assert "Unknown command" in out["messages"][0]["content"]
    assert fake_model.ainvoke.call_count == 0


async def test_respond_node_clarification_prepends_system_message() -> None:
    captured: dict[str, Any] = {}

    async def capture(payload: Any, /) -> AIMessage:
        captured["payload"] = payload
        return AIMessage(content="clarified reply")

    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(side_effect=capture)
    with patch("monet.orchestration.chat_graph._load_model", return_value=fake_model):
        await respond_node(
            {
                "messages": [{"role": "user", "content": "do thing"}],
                "command_meta": {"clarification_prompt": "Ask for scope."},
            }
        )
    assert captured["payload"][0]["role"] == "system"
    assert captured["payload"][0]["content"] == "Ask for scope."


# --- planner_node (single invocation per visit) --------------------------


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


async def test_planner_node_passes_full_context_no_truncation() -> None:
    big = "y" * 5000
    messages = [
        {"role": "user", "content": big},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "plan a thing"},
    ]
    captured: dict[str, Any] = {}

    async def fake_invoke(agent_id: str, **kwargs: Any) -> Any:
        captured["agent_id"] = agent_id
        captured["kwargs"] = kwargs
        return _result(output={"kind": "plan", "goal": "Drafted plan"})

    with patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke):
        out = await planner_node(
            {"messages": messages, "command_meta": {"task": "plan a thing"}}
        )

    assert captured["agent_id"] == "planner"
    assert captured["kwargs"]["command"] == "plan"
    assert captured["kwargs"]["task"] == "plan a thing"
    ctx = captured["kwargs"]["context"]
    # Full transcript pass-through — 2 chat_history entries, no feedback/force.
    assert len(ctx) == 2
    assert ctx[0]["content"] == big
    # Planner output stashed in state for approval_node to consume.
    assert out["last_plan_output"]["goal"] == "Drafted plan"
    assert out["pending_questions"] is None


async def test_planner_node_questions_signal_writes_pending_questions() -> None:
    """NEEDS_CLARIFICATION signal + questions output → state.pending_questions."""

    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        return _result(
            output={"kind": "questions", "questions": ["scope?", "format?"]},
            signals=[
                {
                    "type": SignalType.NEEDS_CLARIFICATION,
                    "reason": "underspec",
                    "metadata": None,
                }
            ],
        )

    with patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke):
        out = await planner_node(
            {
                "messages": [{"role": "user", "content": "do something"}],
                "command_meta": {"task": "do something"},
                "followup_attempts": 0,
            }
        )
    assert out["pending_questions"] == ["scope?", "format?"]
    assert out["last_plan_output"] is None


async def test_planner_node_force_plan_after_max_attempts() -> None:
    """followup_attempts >= MAX → force-plan instruction in the context."""
    captured: dict[str, Any] = {}

    async def fake_invoke(*_args: Any, **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return _result(output={"kind": "plan", "goal": "forced"})

    with patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke):
        out = await planner_node(
            {
                "messages": [{"role": "user", "content": "ambiguous"}],
                "command_meta": {"task": "ambiguous"},
                "followup_attempts": MAX_FOLLOWUP_ATTEMPTS,
            }
        )
    ctx = captured["kwargs"]["context"]
    assert any(
        isinstance(c, dict) and c.get("summary") == "Force-plan override" for c in ctx
    )
    assert out["last_plan_output"]["goal"] == "forced"


async def test_planner_node_give_up_when_force_still_asks() -> None:
    """Agent still emits questions on the forced pass → apology message, END."""

    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        return _result(
            output={"kind": "questions", "questions": ["still unclear?"]},
            signals=[
                {
                    "type": SignalType.NEEDS_CLARIFICATION,
                    "reason": "x",
                    "metadata": None,
                }
            ],
        )

    with patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke):
        out = await planner_node(
            {
                "messages": [{"role": "user", "content": "vague"}],
                "command_meta": {"task": "vague"},
                "followup_attempts": MAX_FOLLOWUP_ATTEMPTS,
            }
        )
    assert out["pending_questions"] is None
    assert out["last_plan_output"] is None
    assert any(
        "couldn't produce a plan" in m["content"].lower() for m in out["messages"]
    )


async def test_planner_node_injects_plan_feedback() -> None:
    """plan_feedback from a prior revise is injected as an instruction entry."""
    captured: dict[str, Any] = {}

    async def fake_invoke(*_args: Any, **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return _result(output={"kind": "plan", "goal": "p"})

    with patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke):
        out = await planner_node(
            {
                "messages": [{"role": "user", "content": "x"}],
                "command_meta": {"task": "x"},
                "plan_feedback": "tighten scope",
            }
        )
    ctx = captured["kwargs"]["context"]
    assert any(
        c.get("type") == "instruction" and "tighten scope" in str(c.get("content"))
        for c in ctx
    )
    # Feedback cleared after consumption so it doesn't re-apply.
    assert out["plan_feedback"] is None


async def test_planner_node_injects_followup_answers() -> None:
    """followup_answers are forwarded as user_clarification context entries."""
    captured: dict[str, Any] = {}

    async def fake_invoke(*_args: Any, **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return _result(output={"kind": "plan", "goal": "p"})

    answers = [
        {
            "type": "user_clarification",
            "summary": "scope?",
            "content": "Q: scope?\nA: small",
        }
    ]
    with patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke):
        out = await planner_node(
            {
                "messages": [{"role": "user", "content": "x"}],
                "command_meta": {"task": "x"},
                "followup_answers": answers,
            }
        )
    ctx = captured["kwargs"]["context"]
    assert any(c.get("type") == "user_clarification" for c in ctx)
    assert out["followup_answers"] is None  # consumed


async def test_planner_node_surfaces_invoke_error_as_assistant_message() -> None:
    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("no planner/plan in manifest")

    with patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke):
        out = await planner_node(
            {
                "messages": [{"role": "user", "content": "x"}],
                "command_meta": {"task": "x"},
            }
        )
    assert "Planner invocation failed" in out["messages"][0]["content"]
    assert out["last_plan_output"] is None


# --- questionnaire_node --------------------------------------------------


async def test_questionnaire_node_interrupts_and_bumps_attempts() -> None:
    with patch(
        "monet.orchestration.chat_graph.interrupt",
        return_value={"q0": "small scope", "q1": "__skip__"},
    ):
        out = await questionnaire_node(
            {
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


async def test_questionnaire_node_no_pending_is_no_op() -> None:
    """Defensive — routed here without questions → clear flags + pass through."""
    out = await questionnaire_node({"followup_attempts": 2})
    assert out == {"pending_questions": None, "followup_attempts": 2}


# --- approval_node -------------------------------------------------------


async def test_approval_node_approve_terminates() -> None:
    plan = {"goal": "Do it", "routing_skeleton": {"goal": "Do it", "nodes": []}}
    with patch(
        "monet.orchestration.chat_graph.interrupt",
        return_value={"action": "approve"},
    ):
        out = await approval_node({"last_plan_output": plan})
    assert out["last_plan_output"] is None
    assert any("approved" in m["content"].lower() for m in out["messages"])
    assert "plan_feedback" not in out  # no revise


async def test_approval_node_reject_terminates() -> None:
    plan = {"goal": "Do it"}
    with patch(
        "monet.orchestration.chat_graph.interrupt",
        return_value={"action": "reject"},
    ):
        out = await approval_node({"last_plan_output": plan})
    assert any("rejected" in m["content"].lower() for m in out["messages"])
    assert "plan_feedback" not in out


async def test_approval_node_revise_writes_feedback_and_bumps_revisions() -> None:
    plan = {"goal": "Do it"}
    with patch(
        "monet.orchestration.chat_graph.interrupt",
        return_value={"action": "revise", "feedback": "narrow the scope"},
    ):
        out = await approval_node(
            {"last_plan_output": plan, "plan_revisions": 0},
        )
    assert out["plan_feedback"] == "narrow the scope"
    assert out["plan_revisions"] == 1
    assert out["last_plan_output"] is None


async def test_approval_node_revise_without_feedback_treated_as_rejection() -> None:
    plan = {"goal": "Do it"}
    with patch(
        "monet.orchestration.chat_graph.interrupt",
        return_value={"action": "revise", "feedback": ""},
    ):
        out = await approval_node({"last_plan_output": plan})
    assert "plan_feedback" not in out
    assert any("no feedback provided" in m["content"].lower() for m in out["messages"])


async def test_approval_node_max_revisions_stops() -> None:
    plan = {"goal": "Do it"}
    with patch(
        "monet.orchestration.chat_graph.interrupt",
        return_value={"action": "revise", "feedback": "again"},
    ):
        out = await approval_node(
            {"last_plan_output": plan, "plan_revisions": PLAN_MAX_REVISIONS},
        )
    assert "plan_feedback" not in out
    assert any(
        f"exceeded {PLAN_MAX_REVISIONS} revisions" in m["content"].lower()
        for m in out["messages"]
    )


# --- specialist_node -----------------------------------------------------


async def test_specialist_node_invokes_named_agent_and_mode() -> None:
    captured: dict[str, Any] = {}

    async def fake_invoke(agent_id: str, **kwargs: Any) -> Any:
        captured["agent_id"] = agent_id
        captured["kwargs"] = kwargs
        return _result(output="deep result")

    with patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke):
        await specialist_node(
            {
                "messages": [{"role": "user", "content": "/researcher:deep x"}],
                "command_meta": {
                    "specialist": "researcher",
                    "mode": "deep",
                    "task": "x",
                },
            }
        )
    assert captured["agent_id"] == "researcher"
    assert captured["kwargs"]["command"] == "deep"


async def test_specialist_node_surfaces_artifact_links() -> None:
    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        return _result(
            output="deep research content",
            artifacts=({"artifact_id": "abc123", "url": "", "key": "report"},),
        )

    meta = {"specialist": "researcher", "mode": "deep", "task": "x"}
    with patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke):
        out = await specialist_node(
            {
                "messages": [{"role": "user", "content": "/researcher:deep x"}],
                "command_meta": meta,
            }
        )
    content = out["messages"][0]["content"]
    assert "deep research content" in content
    assert "→ artifact (report):" in content
    assert "abc123" in content


async def test_specialist_node_missing_capability_inline_message() -> None:
    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("Agent 'unknown/fast' not found in manifest.")

    with patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke):
        out = await specialist_node(
            {
                "messages": [{"role": "user", "content": "/unknown:fast x"}],
                "command_meta": {"specialist": "unknown", "mode": "fast", "task": "x"},
            }
        )
    assert "unavailable" in out["messages"][0]["content"].lower()


# --- compiled-graph routing integration ----------------------------------


async def test_compiled_graph_free_form_triage_routes_to_respond() -> None:
    fake_triage_result = ChatTriageResult(route="chat", confidence=0.95)
    triage_llm = _fake_triage_model(fake_triage_result)

    fake_respond = MagicMock()
    fake_respond.ainvoke = AsyncMock(return_value=AIMessage(content="hi"))

    def pick(model_str: str) -> Any:
        if "lite" in model_str:
            return triage_llm
        return fake_respond

    with patch("monet.orchestration.chat_graph._load_model", side_effect=pick):
        graph = build_chat_graph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "hello"}]},
            config={"configurable": {"thread_id": "chat-1"}},
        )
    contents = [m["content"] for m in out["messages"]]
    assert "hi" in contents


async def test_compiled_graph_slash_plan_approve_flow() -> None:
    """End-to-end: /plan → planner → approval → approve → END."""
    triage_llm = MagicMock()
    triage_llm.with_structured_output = MagicMock()

    async def fake_invoke(agent_id: str, **kwargs: Any) -> Any:
        assert agent_id == "planner"
        assert kwargs["command"] == "plan"
        return _result(output={"kind": "plan", "goal": "Did it"})

    with (
        patch("monet.orchestration.chat_graph._load_model", return_value=triage_llm),
        patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke),
        patch(
            "monet.orchestration.chat_graph.interrupt",
            return_value={"action": "approve"},
        ),
    ):
        graph = build_chat_graph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "/plan draft something"}]},
            config={"configurable": {"thread_id": "chat-p1"}},
        )
    assert triage_llm.with_structured_output.call_count == 0
    assert any("approved" in m["content"].lower() for m in out["messages"])


async def test_compiled_graph_questionnaire_then_plan_flow() -> None:
    """Planner asks → questionnaire runs → planner plans → approval approves."""
    triage_llm = MagicMock()
    triage_llm.with_structured_output = MagicMock()

    invocations = {"count": 0}

    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        invocations["count"] += 1
        if invocations["count"] == 1:
            return _result(
                output={"kind": "questions", "questions": ["topic?"]},
                signals=[
                    {
                        "type": SignalType.NEEDS_CLARIFICATION,
                        "reason": "x",
                        "metadata": None,
                    }
                ],
            )
        return _result(output={"kind": "plan", "goal": "Planned"})

    interrupts = [
        {"q0": "AI trends"},  # questionnaire answer
        {"action": "approve"},  # approval
    ]

    def fake_interrupt(_payload: Any) -> Any:
        return interrupts.pop(0)

    with (
        patch("monet.orchestration.chat_graph._load_model", return_value=triage_llm),
        patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke),
        patch("monet.orchestration.chat_graph.interrupt", side_effect=fake_interrupt),
    ):
        graph = build_chat_graph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "/plan do a thing"}]},
            config={"configurable": {"thread_id": "chat-q1"}},
        )
    assert invocations["count"] == 2  # first asked, second planned
    assert any("approved" in m["content"].lower() for m in out["messages"])


async def test_compiled_graph_unknown_slash_inline_error() -> None:
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock()
    fake_llm.with_structured_output = MagicMock(return_value=fake_llm)
    with patch("monet.orchestration.chat_graph._load_model", return_value=fake_llm):
        graph = build_chat_graph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "/what"}]},
            config={"configurable": {"thread_id": "chat-3"}},
        )
    assert fake_llm.ainvoke.call_count == 0
    assert any("Unknown command" in m["content"] for m in out["messages"])
