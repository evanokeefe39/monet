"""Tests for the inject_plan_context before_agent hook."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from monet import get_artifacts
from monet.artifacts import InMemoryArtifactClient, configure_artifacts
from monet.hooks.plan_context import inject_plan_context
from monet.orchestration._state import WorkBrief, WorkBriefNode


@pytest.fixture
def _artifacts() -> Any:
    configure_artifacts(InMemoryArtifactClient())
    yield
    configure_artifacts(None)


def _ctx(context: list[dict[str, Any]], task: str = "") -> dict[str, Any]:
    return {
        "task": task,
        "context": context,
        "command": "deep",
        "trace_id": "t1",
        "run_id": "r1",
        "agent_id": "writer",
        "skills": [],
    }


def _meta(agent_id: str = "writer", command: str = "deep") -> dict[str, str]:
    return {"agent_id": agent_id, "command": command}


async def _write_brief(brief: WorkBrief) -> dict[str, str]:
    pointer = await get_artifacts().write(
        content=brief.model_dump_json().encode(),
        content_type="application/json",
        summary="test brief",
        confidence=1.0,
        completeness="complete",
        key="work_brief",
    )
    # ArtifactPointer.__getitem__ returns object; cast through items().
    return {str(k): str(v) for k, v in pointer.items()}


async def test_hook_injects_task_from_node(_artifacts: Any) -> None:
    brief = WorkBrief(
        goal="Write a report",
        nodes=[
            WorkBriefNode(
                id="draft",
                depends_on=[],
                agent_id="writer",
                command="deep",
                task="Draft the intro",
            ),
        ],
    )
    pointer = await _write_brief(brief)
    ctx = _ctx(
        [{"type": "plan_item", "work_brief_pointer": pointer, "node_id": "draft"}]
    )
    result = await inject_plan_context(ctx, _meta())  # type: ignore[arg-type]
    assert result["task"] == "Draft the intro"
    assert any(e.get("type") == "plan_goal" for e in result["context"])
    # plan_item entry stripped
    assert not any(e.get("type") == "plan_item" for e in result["context"])


async def test_hook_no_op_without_plan_item(_artifacts: Any) -> None:
    ctx = _ctx([{"type": "note", "content": "just a note"}], task="original task")
    result = await inject_plan_context(ctx, _meta())  # type: ignore[arg-type]
    assert result is ctx


async def test_hook_unknown_node_raises(_artifacts: Any) -> None:
    brief = WorkBrief(
        goal="Test",
        nodes=[
            WorkBriefNode(
                id="a", depends_on=[], agent_id="writer", command="deep", task="t"
            ),
        ],
    )
    pointer = await _write_brief(brief)
    ctx = _ctx(
        [{"type": "plan_item", "work_brief_pointer": pointer, "node_id": "missing"}]
    )
    with pytest.raises(ValueError, match="'missing' not found"):
        await inject_plan_context(ctx, _meta())  # type: ignore[arg-type]


async def test_hook_invalid_artifact_raises(_artifacts: Any) -> None:
    # Write malformed JSON that can't validate as WorkBrief.
    pointer = await get_artifacts().write(
        content=b'{"goal": "x"}',  # missing nodes field
        content_type="application/json",
        summary="bad",
        confidence=1.0,
        completeness="complete",
        key="work_brief",
    )
    ctx = _ctx(
        [
            {
                "type": "plan_item",
                "work_brief_pointer": dict(pointer),
                "node_id": "x",
            }
        ]
    )
    with pytest.raises(ValidationError):
        await inject_plan_context(ctx, _meta())  # type: ignore[arg-type]


async def test_hook_preserves_other_context(_artifacts: Any) -> None:
    brief = WorkBrief(
        goal="Goal",
        nodes=[
            WorkBriefNode(
                id="draft",
                depends_on=[],
                agent_id="writer",
                command="deep",
                task="do it",
            ),
        ],
    )
    pointer = await _write_brief(brief)
    other_entry = {"type": "note", "content": "keep me"}
    ctx = _ctx(
        [
            other_entry,
            {"type": "plan_item", "work_brief_pointer": pointer, "node_id": "draft"},
        ]
    )
    result = await inject_plan_context(ctx, _meta())  # type: ignore[arg-type]
    # Other context entries preserved, plan_goal added, plan_item stripped.
    types = [e.get("type") for e in result["context"]]
    assert "note" in types
    assert "plan_goal" in types
    assert "plan_item" not in types


async def test_hook_registered_in_default_registry() -> None:
    """Importing monet.hooks registers the hook in the default registry."""
    import monet.hooks  # noqa: F401
    from monet.core.hooks import default_hook_registry

    hooks = default_hook_registry.lookup("before_agent", "writer", "deep")
    assert any(h.handler.__name__ == "inject_plan_context" for h in hooks), (
        "inject_plan_context must be registered for all agents"
    )
