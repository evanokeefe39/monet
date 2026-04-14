"""Tests for RoutingSkeleton and WorkBrief Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from monet.orchestration._state import (
    RoutingNode,
    RoutingSkeleton,
    WorkBrief,
    WorkBriefNode,
)

# --- RoutingSkeleton validation ---


def test_valid_dag() -> None:
    skeleton = RoutingSkeleton(
        goal="Write a report",
        nodes=[
            RoutingNode(
                id="research", depends_on=[], agent_id="researcher", command="deep"
            ),
            RoutingNode(
                id="draft", depends_on=["research"], agent_id="writer", command="draft"
            ),
        ],
    )
    assert len(skeleton.nodes) == 2


def test_single_node() -> None:
    skeleton = RoutingSkeleton(
        goal="Simple task",
        nodes=[
            RoutingNode(id="do_it", depends_on=[], agent_id="worker", command="run")
        ],
    )
    assert len(skeleton.nodes) == 1


def test_empty_nodes_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one node"):
        RoutingSkeleton(goal="Empty", nodes=[])


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="Duplicate node id 'a'"):
        RoutingSkeleton(
            goal="Dup",
            nodes=[
                RoutingNode(id="a", depends_on=[], agent_id="x", command="y"),
                RoutingNode(id="a", depends_on=[], agent_id="x", command="y"),
            ],
        )


def test_dangling_dependency_rejected() -> None:
    with pytest.raises(ValidationError, match="depends on unknown node 'missing'"):
        RoutingSkeleton(
            goal="Dangling",
            nodes=[
                RoutingNode(id="a", depends_on=["missing"], agent_id="x", command="y"),
            ],
        )


def test_cycle_rejected() -> None:
    with pytest.raises(ValidationError, match="Cycle detected"):
        RoutingSkeleton(
            goal="Cycle",
            nodes=[
                RoutingNode(id="a", depends_on=["b"], agent_id="x", command="y"),
                RoutingNode(id="b", depends_on=["a"], agent_id="x", command="y"),
            ],
        )


def test_self_cycle_rejected() -> None:
    with pytest.raises(ValidationError, match="Cycle detected"):
        RoutingSkeleton(
            goal="Self",
            nodes=[
                RoutingNode(id="a", depends_on=["a"], agent_id="x", command="y"),
            ],
        )


def test_three_node_cycle_rejected() -> None:
    with pytest.raises(ValidationError, match="Cycle detected"):
        RoutingSkeleton(
            goal="Triangle",
            nodes=[
                RoutingNode(id="a", depends_on=["c"], agent_id="x", command="y"),
                RoutingNode(id="b", depends_on=["a"], agent_id="x", command="y"),
                RoutingNode(id="c", depends_on=["b"], agent_id="x", command="y"),
            ],
        )


# --- ready_nodes ---


def test_ready_nodes_roots() -> None:
    skeleton = RoutingSkeleton(
        goal="Test",
        nodes=[
            RoutingNode(id="a", depends_on=[], agent_id="x", command="y"),
            RoutingNode(id="b", depends_on=[], agent_id="x", command="y"),
            RoutingNode(id="c", depends_on=["a", "b"], agent_id="x", command="y"),
        ],
    )
    ready = skeleton.ready_nodes(set())
    assert {n.id for n in ready} == {"a", "b"}


def test_ready_nodes_after_partial_completion() -> None:
    skeleton = RoutingSkeleton(
        goal="Test",
        nodes=[
            RoutingNode(id="a", depends_on=[], agent_id="x", command="y"),
            RoutingNode(id="b", depends_on=[], agent_id="x", command="y"),
            RoutingNode(id="c", depends_on=["a", "b"], agent_id="x", command="y"),
        ],
    )
    ready = skeleton.ready_nodes({"a"})
    assert {n.id for n in ready} == {"b"}


def test_ready_nodes_fan_in() -> None:
    skeleton = RoutingSkeleton(
        goal="Test",
        nodes=[
            RoutingNode(id="a", depends_on=[], agent_id="x", command="y"),
            RoutingNode(id="b", depends_on=[], agent_id="x", command="y"),
            RoutingNode(id="c", depends_on=["a", "b"], agent_id="x", command="y"),
        ],
    )
    ready = skeleton.ready_nodes({"a", "b"})
    assert {n.id for n in ready} == {"c"}


def test_ready_nodes_empty_when_all_complete() -> None:
    skeleton = RoutingSkeleton(
        goal="Test",
        nodes=[RoutingNode(id="a", depends_on=[], agent_id="x", command="y")],
    )
    assert skeleton.ready_nodes({"a"}) == []


# --- is_complete ---


def test_is_complete_false() -> None:
    skeleton = RoutingSkeleton(
        goal="Test",
        nodes=[
            RoutingNode(id="a", depends_on=[], agent_id="x", command="y"),
            RoutingNode(id="b", depends_on=["a"], agent_id="x", command="y"),
        ],
    )
    assert skeleton.is_complete({"a"}) is False


def test_is_complete_true() -> None:
    skeleton = RoutingSkeleton(
        goal="Test",
        nodes=[
            RoutingNode(id="a", depends_on=[], agent_id="x", command="y"),
            RoutingNode(id="b", depends_on=["a"], agent_id="x", command="y"),
        ],
    )
    assert skeleton.is_complete({"a", "b"}) is True


# --- WorkBrief ---


def test_work_brief_valid() -> None:
    brief = WorkBrief(
        goal="Write report",
        nodes=[
            WorkBriefNode(
                id="research",
                depends_on=[],
                agent_id="researcher",
                command="deep",
                task="Research competitors",
            ),
            WorkBriefNode(
                id="draft",
                depends_on=["research"],
                agent_id="writer",
                command="draft",
                task="Draft the report",
            ),
        ],
        assumptions=["Assume English output"],
    )
    assert len(brief.nodes) == 2
    assert brief.assumptions == ["Assume English output"]


def test_work_brief_empty_nodes_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one node"):
        WorkBrief(goal="Empty", nodes=[])


def test_work_brief_dangling_dep_rejected() -> None:
    with pytest.raises(ValidationError, match="depends on unknown node"):
        WorkBrief(
            goal="Dangling",
            nodes=[
                WorkBriefNode(
                    id="a", depends_on=["missing"], agent_id="x", command="y", task="t"
                ),
            ],
        )


def test_work_brief_duplicate_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="Duplicate node id"):
        WorkBrief(
            goal="Dup",
            nodes=[
                WorkBriefNode(
                    id="a", depends_on=[], agent_id="x", command="y", task="t1"
                ),
                WorkBriefNode(
                    id="a", depends_on=[], agent_id="x", command="y", task="t2"
                ),
            ],
        )


# --- to_routing_skeleton ---


def test_to_routing_skeleton_strips_task() -> None:
    brief = WorkBrief(
        goal="Test",
        nodes=[
            WorkBriefNode(
                id="a",
                depends_on=[],
                agent_id="researcher",
                command="deep",
                task="Do research",
            ),
            WorkBriefNode(
                id="b",
                depends_on=["a"],
                agent_id="writer",
                command="draft",
                task="Write it",
            ),
        ],
    )
    skeleton = brief.to_routing_skeleton()
    assert skeleton.goal == "Test"
    assert len(skeleton.nodes) == 2
    assert skeleton.nodes[0].id == "a"
    assert skeleton.nodes[0].agent_id == "researcher"
    assert skeleton.nodes[1].depends_on == ["a"]
    # RoutingNode has no task field
    assert not hasattr(skeleton.nodes[0], "task")


def test_to_routing_skeleton_preserves_structure() -> None:
    brief = WorkBrief(
        goal="Complex",
        nodes=[
            WorkBriefNode(id="a", depends_on=[], agent_id="x", command="c1", task="t1"),
            WorkBriefNode(id="b", depends_on=[], agent_id="y", command="c2", task="t2"),
            WorkBriefNode(
                id="c", depends_on=["a", "b"], agent_id="z", command="c3", task="t3"
            ),
        ],
    )
    skeleton = brief.to_routing_skeleton()
    assert skeleton.nodes[2].depends_on == ["a", "b"]


# --- Pydantic round-trip ---


def test_routing_skeleton_round_trip() -> None:
    skeleton = RoutingSkeleton(
        goal="Test",
        nodes=[
            RoutingNode(id="a", depends_on=[], agent_id="x", command="y"),
            RoutingNode(id="b", depends_on=["a"], agent_id="x", command="y"),
        ],
    )
    dumped = skeleton.model_dump()
    restored = RoutingSkeleton.model_validate(dumped)
    assert restored.goal == skeleton.goal
    assert len(restored.nodes) == 2
    assert restored.nodes[1].depends_on == ["a"]


def test_work_brief_round_trip() -> None:
    brief = WorkBrief(
        goal="Test",
        nodes=[
            WorkBriefNode(
                id="a", depends_on=[], agent_id="x", command="y", task="do it"
            ),
        ],
        assumptions=["assume nothing"],
        is_sensitive=True,
    )
    dumped = brief.model_dump()
    restored = WorkBrief.model_validate(dumped)
    assert restored.is_sensitive is True
    assert restored.assumptions == ["assume nothing"]
