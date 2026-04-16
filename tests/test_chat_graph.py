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
    ChatState,
    ChatTriageResult,
    _build_context,
    _parse_slash,
    build_chat_graph,
    parse_command_node,
    planner_node,
    respond_node,
    specialist_node,
    triage_node,
)

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


async def test_triage_node_routes_planner() -> None:
    fake = _fake_triage_model(ChatTriageResult(route="planner", confidence=0.9))
    with patch("monet.orchestration.chat_graph._load_model", return_value=fake):
        out = await triage_node(
            {"messages": [{"role": "user", "content": "plan a feature"}]}
        )
    assert out["route"] == "planner"
    assert out["command_meta"]["task"] == "plan a feature"


async def test_triage_node_routes_specialist_with_name() -> None:
    fake = _fake_triage_model(
        ChatTriageResult(route="specialist", specialist="researcher", confidence=0.8)
    )
    with (
        patch("monet.orchestration.chat_graph._load_model", return_value=fake),
        patch(
            "monet.orchestration.chat_graph._known_agent_ids",
            return_value={"researcher"},
        ),
    ):
        out = await triage_node({"messages": [{"role": "user", "content": "research"}]})
    assert out["route"] == "specialist"
    assert out["command_meta"]["specialist"] == "researcher"
    assert out["command_meta"]["mode"] == "fast"


async def test_triage_node_rejects_hallucinated_specialist() -> None:
    fake = _fake_triage_model(
        ChatTriageResult(
            route="specialist",
            specialist="ai_trends_healthcare",
            confidence=0.5,
        )
    )
    with (
        patch("monet.orchestration.chat_graph._load_model", return_value=fake),
        patch(
            "monet.orchestration.chat_graph._known_agent_ids",
            return_value={"researcher", "writer"},
        ),
    ):
        out = await triage_node({"messages": [{"role": "user", "content": "research"}]})
    assert out["route"] == "planner"


async def test_triage_node_clarification_routes_chat() -> None:
    fake = _fake_triage_model(
        ChatTriageResult(
            route="planner",
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


# --- planner_node / specialist_node --------------------------------------


async def _fake_result(output: Any = "plan ok", success: bool = True) -> MagicMock:
    result = MagicMock()
    result.success = success
    result.output = output
    result.signals = []
    return result


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
        return await _fake_result(output={"goal": "Drafted plan"})

    with (
        patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke),
        patch(
            "monet.orchestration.chat_graph.interrupt",
            return_value={"action": "approve"},
        ),
    ):
        out = await planner_node(
            {"messages": messages, "command_meta": {"task": "plan a thing"}}
        )

    assert captured["agent_id"] == "planner"
    assert captured["kwargs"]["command"] == "plan"
    assert captured["kwargs"]["task"] == "plan a thing"
    ctx = captured["kwargs"]["context"]
    assert len(ctx) == 2
    assert ctx[0]["content"] == big  # no truncation
    assert "Drafted plan" in out["messages"][0]["content"]
    assert any("approved" in m["content"].lower() for m in out["messages"])


async def test_planner_node_revise_loop_re_invokes_with_feedback() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_invoke(agent_id: str, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await _fake_result(output={"goal": "Plan"})

    decisions = [
        {"action": "revise", "feedback": "tighten scope"},
        {"action": "approve"},
    ]

    def fake_interrupt(_payload: Any) -> Any:
        return decisions.pop(0)

    with (
        patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke),
        patch("monet.orchestration.chat_graph.interrupt", side_effect=fake_interrupt),
    ):
        out = await planner_node(
            {
                "messages": [{"role": "user", "content": "/plan x"}],
                "command_meta": {"task": "x"},
            }
        )

    assert len(calls) == 2
    second_ctx = calls[1]["context"]
    assert any(
        isinstance(c, dict)
        and c.get("type") == "instruction"
        and "tighten scope" in str(c.get("content"))
        for c in second_ctx
    )
    assert any("approved" in m["content"].lower() for m in out["messages"])


async def test_planner_node_reject_halts() -> None:
    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        return await _fake_result(output={"goal": "Plan"})

    with (
        patch("monet.orchestration.chat_graph.invoke_agent", side_effect=fake_invoke),
        patch(
            "monet.orchestration.chat_graph.interrupt",
            return_value={"action": "reject"},
        ),
    ):
        out = await planner_node(
            {
                "messages": [{"role": "user", "content": "/plan x"}],
                "command_meta": {"task": "x"},
            }
        )
    assert any("rejected" in m["content"].lower() for m in out["messages"])


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


async def test_specialist_node_invokes_named_agent_and_mode() -> None:
    captured: dict[str, Any] = {}

    async def fake_invoke(agent_id: str, **kwargs: Any) -> Any:
        captured["agent_id"] = agent_id
        captured["kwargs"] = kwargs
        return await _fake_result(output="deep result")

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
        # triage model = flash-lite, respond model = flash
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


async def test_compiled_graph_slash_plan_bypasses_triage() -> None:
    triage_llm = MagicMock()
    triage_llm.with_structured_output = MagicMock()

    async def fake_invoke(agent_id: str, **kwargs: Any) -> Any:
        assert agent_id == "planner"
        assert kwargs["command"] == "plan"
        return await _fake_result(output={"goal": "Did it"})

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
            config={"configurable": {"thread_id": "chat-2"}},
        )
    # Triage's structured output never called — slash path bypassed it.
    assert triage_llm.with_structured_output.call_count == 0
    assert any("Did it" in m["content"] for m in out["messages"])


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
    # No LLM call — inline error path.
    assert fake_llm.ainvoke.call_count == 0
    assert any("Unknown command" in m["content"] for m in out["messages"])
