# mypy: disable-error-code="call-overload,arg-type"
"""End-to-end chat graph composition + client-decoupling pins.

Covers two concerns that the refactor introduced:

1. **Name-matching contract** between ``ChatState`` and the mounted
   planning + execution subgraphs. A field rename on
   ``work_brief_pointer`` / ``routing_skeleton`` would break the flow
   from planning → execution silently; this test fails loudly instead.

2. **Graph-agnostic client + REPL**: ``monet.client`` and
   ``monet.cli`` must never import from ``monet.orchestration.chat``
   internals. The chat graph is one of many possible chat
   implementations (users can swap via ``[chat] graph = "…"``); the
   client + REPL stay coupled only to public event / form-schema
   shapes, never to a specific graph's state fields.
"""

from __future__ import annotations

import pkgutil
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import MemorySaver

from monet.orchestration.chat import build_chat_graph


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
    node_agent: str = "researcher",
) -> MagicMock:
    skeleton = {
        "goal": goal,
        "nodes": [
            {
                "id": "n1",
                "agent_id": node_agent,
                "command": "fast",
                "depends_on": [],
            }
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


async def test_plan_approve_flow_pins_subgraph_name_matching() -> None:
    """Assert work_brief_pointer + routing_skeleton survive planning → execution.

    The ChatState ↔ PlanningState ↔ ExecutionState key contract is
    structural (TypedDict inheritance + same-name fields). A field
    rename would fail this test even though every node still works in
    isolation.
    """
    triage_llm = MagicMock()
    triage_llm.with_structured_output = MagicMock()

    async def fake_planning_invoke(agent_id: str, **kwargs: Any) -> Any:
        assert agent_id == "planner"
        return _plan_result(goal="pins contract", artifact_id="brief-pin")

    captured_exec: dict[str, Any] = {}

    async def fake_exec_invoke(agent_id: str, **kwargs: Any) -> Any:
        # Execution receives work_brief_pointer via context entries;
        # capture the plan_item context to prove the pointer traversed
        # the ChatState → ExecutionState boundary.
        for entry in kwargs.get("context") or []:
            if isinstance(entry, dict) and entry.get("type") == "plan_item":
                captured_exec["pointer"] = entry.get("work_brief_pointer")
                captured_exec["node_id"] = entry.get("node_id")
        return _result(
            output=f"{agent_id} ran",
            artifacts=({"artifact_id": "out-1", "url": "/v1/out-1", "key": "result"},),
        )

    with (
        patch(
            "monet.orchestration.chat._lc._load_model",
            return_value=triage_llm,
        ),
        patch(
            "monet.orchestration.planning_graph.invoke_agent",
            side_effect=fake_planning_invoke,
        ),
        patch(
            "monet.orchestration.execution_graph.invoke_agent",
            side_effect=fake_exec_invoke,
        ),
        patch(
            "monet.orchestration.planning_graph.interrupt",
            return_value={"action": "approve"},
        ),
    ):
        graph = build_chat_graph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "/plan pin the contract"}]},
            config={"configurable": {"thread_id": "e2e-1"}},
        )

    # Subgraph name-matching contract: pointer flowed all the way to the
    # execution agent invocation.
    assert captured_exec.get("pointer", {}).get("artifact_id") == "brief-pin"
    assert captured_exec.get("node_id") == "n1"

    # Final state carries plan_approved + wave_results.
    assert out.get("plan_approved") is True
    assert out.get("work_brief_pointer", {}).get("artifact_id") == "brief-pin"
    assert len(out.get("wave_results") or []) == 1
    assert any(
        "Execution finished" in (m.get("content") or "")
        for m in out.get("messages") or []
    )


async def test_plan_reject_flow_skips_execution() -> None:
    """Reject at approval must NOT invoke the execution subgraph."""
    triage_llm = MagicMock()
    triage_llm.with_structured_output = MagicMock()

    async def fake_planning_invoke(agent_id: str, **kwargs: Any) -> Any:
        return _plan_result()

    exec_invoke = AsyncMock()

    with (
        patch(
            "monet.orchestration.chat._lc._load_model",
            return_value=triage_llm,
        ),
        patch(
            "monet.orchestration.planning_graph.invoke_agent",
            side_effect=fake_planning_invoke,
        ),
        patch(
            "monet.orchestration.execution_graph.invoke_agent",
            side_effect=exec_invoke,
        ),
        patch(
            "monet.orchestration.planning_graph.interrupt",
            return_value={"action": "reject"},
        ),
    ):
        graph = build_chat_graph().compile(checkpointer=MemorySaver())
        out = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "/plan no thanks"}]},
            config={"configurable": {"thread_id": "e2e-2"}},
        )

    assert exec_invoke.await_count == 0
    assert out.get("plan_approved") is False
    assert not out.get("wave_results")


# --- Client / REPL decoupling ------------------------------------------


def _iter_source_files(package_dir: Path) -> list[Path]:
    return [p for p in package_dir.rglob("*.py") if p.is_file()]


def test_client_does_not_import_chat_internals() -> None:
    """``monet.client.*`` must stay graph-agnostic.

    A client that imports ``monet.orchestration.chat.*`` would couple
    to one specific chat graph implementation, defeating the point of
    ``[chat] graph = "module:factory"`` configuration.
    """
    import monet.client as client_pkg

    assert client_pkg.__file__ is not None
    client_dir = Path(client_pkg.__file__).parent
    for path in _iter_source_files(client_dir):
        text = path.read_text(encoding="utf-8")
        assert "monet.orchestration.chat" not in text, (
            f"{path} imports monet.orchestration.chat — client must stay graph-agnostic"
        )


def test_cli_does_not_import_chat_internals() -> None:
    """``monet.cli.*`` must stay graph-agnostic.

    The REPL (``monet chat``) resolves the chat graph via
    ``ChatConfig.graph`` at boot; it must not hardcode any specific
    chat graph's internals.
    """
    import monet.cli as cli_pkg

    assert cli_pkg.__file__ is not None
    cli_dir = Path(cli_pkg.__file__).parent
    for path in _iter_source_files(cli_dir):
        text = path.read_text(encoding="utf-8")
        assert "monet.orchestration.chat" not in text, (
            f"{path} imports monet.orchestration.chat — CLI must stay graph-agnostic"
        )


def test_chat_subpackage_importable() -> None:
    """Sanity: the chat subpackage advertises its public surface."""
    from monet.orchestration import chat

    assert hasattr(chat, "build_chat_graph")
    assert hasattr(chat, "ChatState")
    assert hasattr(chat, "ChatTriageResult")
    # Every private module is reachable via pkgutil so the split is real.
    submodules = {m.name for m in pkgutil.iter_modules(chat.__path__)}
    assert {
        "_build",
        "_format",
        "_lc",
        "_parse",
        "_respond",
        "_specialist",
        "_state",
        "_triage",
    } <= submodules


def test_chat_graph_shim_loads_via_file_path() -> None:
    """Regression: Aegra loads graph modules by file path, not module name.

    Aegra's graph loader uses ``importlib.util.spec_from_file_location``
    and re-parents the module under a synthetic ``aegra_graphs``
    namespace. A relative import (``from .chat import ...``) in
    ``chat_graph.py`` resolves against that synthetic package and
    fails at boot — which is exactly what broke on first run of
    ``monet dev``. This test simulates that load pattern so the
    breakage surfaces in CI instead of in the user's terminal.
    """
    import importlib.util

    from monet.orchestration import chat_graph

    assert chat_graph.__file__ is not None
    spec = importlib.util.spec_from_file_location(
        "aegra_graphs.chat_graph",
        chat_graph.__file__,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # would raise ImportError if relative
    assert callable(mod.build_chat_graph)
    assert hasattr(mod, "ChatState")
    assert hasattr(mod, "ChatTriageResult")
