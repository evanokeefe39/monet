# mypy: disable-error-code="call-overload,arg-type"
"""Tests for the chat graph — parse, triage, respond, specialist, summary.

Planner / questionnaire / approval behaviour lives in
``build_planning_subgraph`` and is covered by ``test_planning_graph.py``.
Compiled-graph tests here exercise chat's end-to-end composition with
the mounted planning + execution subgraphs.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from monet.orchestration.prebuilt.chat import (
    ChatState,
    ChatTriageResult,
    build_chat_graph,
)
from monet.orchestration.prebuilt.chat._format import execution_summary_node
from monet.orchestration.prebuilt.chat._parse import _parse_slash, parse_command_node
from monet.orchestration.prebuilt.chat._respond import respond_node
from monet.orchestration.prebuilt.chat._specialist import (
    _build_context,
    specialist_node,
)
from monet.orchestration.prebuilt.chat._triage import triage_node
from monet.signals import SignalType

# --- parse_command_node ---------------------------------------------------


def test_parse_slash_no_slash_returns_none_route() -> None:
    assert _parse_slash("hello there") == {"route": None}


def test_parse_slash_plan_extracts_remainder() -> None:
    result = _parse_slash("/plan draft a roadmap")
    assert result == {"route": "planning", "command_meta": {"task": "draft a roadmap"}}


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
    assert out["route"] == "planning"
    assert out["command_meta"]["task"] == "something"
    # task copied into state key so the mounted planning subgraph picks it up.
    assert out["task"] == "something"


async def test_parse_command_node_free_form_copies_task_without_route() -> None:
    state: ChatState = {"messages": [{"role": "user", "content": "tell me a joke"}]}
    out = await parse_command_node(state)
    assert out["route"] is None
    assert out["task"] == "tell me a joke"


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
    with patch("monet.orchestration.prebuilt.chat._lc._load_model", return_value=fake):
        out = await triage_node({"messages": [{"role": "user", "content": "hi"}]})
    assert out["route"] == "chat"


async def test_triage_node_routes_plan_to_planning_edge() -> None:
    """Triage returns route='plan'; the graph edge name is 'planning'."""
    fake = _fake_triage_model(ChatTriageResult(route="plan", confidence=0.9))
    with patch("monet.orchestration.prebuilt.chat._lc._load_model", return_value=fake):
        out = await triage_node(
            {"messages": [{"role": "user", "content": "plan a feature"}]}
        )
    assert out["route"] == "planning"
    assert out["command_meta"]["task"] == "plan a feature"
    assert out["task"] == "plan a feature"


async def test_triage_node_clarification_routes_chat() -> None:
    fake = _fake_triage_model(
        ChatTriageResult(
            route="plan",
            confidence=0.3,
            clarification_needed=True,
            clarification_prompt="Be more specific about the scope.",
        )
    )
    with patch("monet.orchestration.prebuilt.chat._lc._load_model", return_value=fake):
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
    with patch("monet.orchestration.prebuilt.chat._lc._load_model", return_value=model):
        out = await triage_node({"messages": [{"role": "user", "content": "hi"}]})
    assert out["route"] == "chat"


# --- respond_node --------------------------------------------------------


async def test_respond_node_calls_llm_directly_no_invoke_agent() -> None:
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=AIMessage(content="hello back"))
    with (
        patch(
            "monet.orchestration.prebuilt.chat._lc._load_model", return_value=fake_model
        ),
        patch(
            "monet.orchestration.prebuilt.chat._specialist.invoke_agent"
        ) as invoke_mock,
    ):
        out = await respond_node({"messages": [{"role": "user", "content": "hi"}]})
    assert invoke_mock.call_count == 0
    assert out["messages"][0]["content"] == "hello back"


async def test_respond_node_unknown_command_renders_inline_error_without_llm() -> None:
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock()
    with patch(
        "monet.orchestration.prebuilt.chat._lc._load_model", return_value=fake_model
    ):
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
    with patch(
        "monet.orchestration.prebuilt.chat._lc._load_model", return_value=fake_model
    ):
        await respond_node(
            {
                "messages": [{"role": "user", "content": "do thing"}],
                "command_meta": {"clarification_prompt": "Ask for scope."},
            }
        )
    assert captured["payload"][0]["role"] == "system"
    assert captured["payload"][0]["content"] == "Ask for scope."


# --- helpers for planner+execution mocks --------------------------------


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


def _plan_result(
    goal: str = "Do it",
    artifact_id: str = "brief-1",
) -> MagicMock:
    """Shortcut for a valid planner plan result with artifact + skeleton."""
    skeleton = {
        "goal": goal,
        "nodes": [
            {"id": "n1", "agent_id": "researcher", "command": "fast", "depends_on": []}
        ],
    }
    artifact = {
        "artifact_id": artifact_id,
        "url": f"/v1/{artifact_id}",
        "key": "work_brief",
    }
    return _result(
        output={
            "kind": "plan",
            "goal": goal,
            "work_brief_artifact_id": artifact_id,
            "routing_skeleton": skeleton,
        },
        artifacts=(artifact,),
    )


# --- specialist_node -----------------------------------------------------


async def test_specialist_node_invokes_named_agent_and_mode() -> None:
    captured: dict[str, Any] = {}

    async def fake_invoke(agent_id: str, **kwargs: Any) -> Any:
        captured["agent_id"] = agent_id
        captured["kwargs"] = kwargs
        return _result(output="deep result")

    with patch(
        "monet.orchestration.prebuilt.chat._specialist.invoke_agent",
        side_effect=fake_invoke,
    ):
        await specialist_node(
            {
                "messages": [{"role": "user", "content": "/researcher:deep x"}],
                "command_meta": {
                    "specialist": "researcher",
                    "mode": "deep",
                    "task": "x",
                },
            },
            {"configurable": {}},
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
    with patch(
        "monet.orchestration.prebuilt.chat._specialist.invoke_agent",
        side_effect=fake_invoke,
    ):
        out = await specialist_node(
            {
                "messages": [{"role": "user", "content": "/researcher:deep x"}],
                "command_meta": meta,
            },
            {"configurable": {}},
        )
    content = out["messages"][0]["content"]
    assert "deep research content" in content
    assert "→ artifact (report):" in content
    assert "abc123" in content


async def test_specialist_node_missing_capability_inline_message() -> None:
    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("Agent 'unknown/fast' not found in manifest.")

    with patch(
        "monet.orchestration.prebuilt.chat._specialist.invoke_agent",
        side_effect=fake_invoke,
    ):
        out = await specialist_node(
            {
                "messages": [{"role": "user", "content": "/unknown:fast x"}],
                "command_meta": {"specialist": "unknown", "mode": "fast", "task": "x"},
            },
            {"configurable": {}},
        )
    assert "unavailable" in out["messages"][0]["content"].lower()


# --- execution_summary_node ---------------------------------------------


async def test_execution_summary_node_renders_wave_results() -> None:
    state: dict[str, Any] = {
        "wave_results": [
            {
                "id": "research_topic",
                "agent_id": "researcher",
                "success": True,
                "artifacts": [{"artifact_id": "abc-123", "url": "/v1/abc-123"}],
            },
            {
                "id": "qa_report",
                "agent_id": "qa",
                "success": False,
                "artifacts": [],
            },
        ]
    }
    out = await execution_summary_node(state)  # type: ignore[arg-type]
    msg = out["messages"][0]["content"]
    assert "Execution finished" in msg
    assert "ok research_topic (researcher) → " in msg
    assert "abc-123" in msg
    assert "fail qa_report (qa)" in msg


async def test_execution_summary_node_no_results_one_line() -> None:
    out = await execution_summary_node({})  # type: ignore[arg-type]
    assert "no results" in out["messages"][0]["content"].lower()


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

    with patch("monet.orchestration.prebuilt.chat._lc._load_model", side_effect=pick):
        graph = build_chat_graph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "hello"}]},
            config={"configurable": {"thread_id": "chat-1"}},
        )
    contents = [m["content"] for m in out["messages"]]
    assert "hi" in contents


async def test_compiled_graph_slash_plan_approve_flow() -> None:
    """End-to-end: /plan → planning subgraph → approve → execution → summary."""
    triage_llm = MagicMock()
    triage_llm.with_structured_output = MagicMock()

    async def fake_planning_invoke(agent_id: str, **kwargs: Any) -> Any:
        assert agent_id == "planner"
        assert kwargs["command"] == "plan"
        return _plan_result(goal="Did it")

    async def fake_exec_invoke(agent_id: str, **kwargs: Any) -> Any:
        return _result(output=f"{agent_id} ran")

    with (
        patch(
            "monet.orchestration.prebuilt.chat._lc._load_model", return_value=triage_llm
        ),
        patch(
            "monet.orchestration.prebuilt.planning_graph.invoke_agent",
            side_effect=fake_planning_invoke,
        ),
        patch(
            "monet.orchestration.prebuilt.execution_graph.invoke_agent",
            side_effect=fake_exec_invoke,
        ),
        patch(
            "monet.orchestration.prebuilt.planning_graph.interrupt",
            return_value={"action": "approve"},
        ),
    ):
        graph = build_chat_graph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "/plan draft something"}]},
            config={"configurable": {"thread_id": "chat-p1"}},
        )
    assert triage_llm.with_structured_output.call_count == 0
    # Execution summary message shows the mounted execution subgraph ran.
    assert any("Execution finished" in m["content"] for m in out["messages"])


async def test_compiled_graph_questionnaire_then_plan_flow() -> None:
    """Planner asks → questionnaire runs → planner plans → approve → execute."""
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
        return _plan_result(goal="Planned")

    interrupts = [
        {"q0": "AI trends"},  # questionnaire answer
        {"action": "approve"},  # approval
    ]

    def fake_interrupt(_payload: Any) -> Any:
        return interrupts.pop(0)

    async def fake_exec_invoke(agent_id: str, **kwargs: Any) -> Any:
        return _result(output=f"{agent_id} ran")

    with (
        patch(
            "monet.orchestration.prebuilt.chat._lc._load_model", return_value=triage_llm
        ),
        patch(
            "monet.orchestration.prebuilt.planning_graph.invoke_agent",
            side_effect=fake_invoke,
        ),
        patch(
            "monet.orchestration.prebuilt.execution_graph.invoke_agent",
            side_effect=fake_exec_invoke,
        ),
        patch(
            "monet.orchestration.prebuilt.planning_graph.interrupt",
            side_effect=fake_interrupt,
        ),
    ):
        graph = build_chat_graph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "/plan do a thing"}]},
            config={"configurable": {"thread_id": "chat-q1"}},
        )
    assert invocations["count"] == 2  # first asked, second planned
    assert any("Execution finished" in m["content"] for m in out["messages"])


async def test_compiled_graph_unknown_slash_inline_error() -> None:
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock()
    fake_llm.with_structured_output = MagicMock(return_value=fake_llm)
    with patch(
        "monet.orchestration.prebuilt.chat._lc._load_model", return_value=fake_llm
    ):
        graph = build_chat_graph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "/what"}]},
            config={"configurable": {"thread_id": "chat-3"}},
        )
    assert fake_llm.ainvoke.call_count == 0
    assert any("Unknown command" in m["content"] for m in out["messages"])
