"""Lean LangGraph state schema.

Full artifact content never lives in graph state.
Only summaries, pointers, confidence, and signals.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, model_validator

# ArtifactPointer must be a runtime import, not TYPE_CHECKING, because
# LangGraph uses get_type_hints() to introspect state schemas at
# StateGraph construction time.
from monet.types import ArtifactPointer, Signal, SignalType  # noqa: F401, TC001


def _append_reducer(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reducer that appends new entries to existing list."""
    return existing + new


def _int_append_reducer(existing: list[int], new: list[int]) -> list[int]:
    """Reducer that appends int entries to existing list."""
    return existing + new


# --- Routing graph (flat DAG) ---


class RoutingNode(BaseModel):
    """A single agent invocation in the execution graph.

    id: human-readable, unique within this brief.
        e.g. "research_competitors", "draft_report"
    depends_on: list of node ids that must complete before
        this node runs. Empty list = root node (runs immediately).
    agent_id: registered agent identifier.
    command: agent command to invoke.
    """

    id: str
    depends_on: list[str] = []
    agent_id: str
    command: str


class RoutingSkeleton(BaseModel):
    """Flat routing graph returned inline by the planner.

    Contains only what the execution graph needs to sequence work.
    No task content. No sensitive data. Lives in LangGraph state
    and the checkpoint.

    Nodes form a DAG. Nodes with no depends_on are roots and run
    immediately. Nodes whose depends_on are all complete run next.
    Cycles and dangling references are rejected at validation time.
    """

    goal: str
    nodes: list[RoutingNode]

    @model_validator(mode="after")
    def validate_graph(self) -> RoutingSkeleton:
        """Validate DAG: non-empty, unique ids, no dangling deps, no cycles."""
        if not self.nodes:
            raise ValueError("Routing skeleton must contain at least one node")
        ids = {n.id for n in self.nodes}
        seen_ids: set[str] = set()
        for node in self.nodes:
            if node.id in seen_ids:
                raise ValueError(f"Duplicate node id '{node.id}'")
            seen_ids.add(node.id)
            for dep in node.depends_on:
                if dep not in ids:
                    raise ValueError(
                        f"Node '{node.id}' depends on unknown node '{dep}'"
                    )
        self._check_no_cycles(ids)
        return self

    def _check_no_cycles(self, ids: set[str]) -> None:
        dep_map = {n.id: set(n.depends_on) for n in self.nodes}
        visited: set[str] = set()
        in_stack: set[str] = set()

        def dfs(node_id: str) -> None:
            visited.add(node_id)
            in_stack.add(node_id)
            for dep in dep_map.get(node_id, set()):
                if dep not in visited:
                    dfs(dep)
                elif dep in in_stack:
                    raise ValueError(f"Cycle detected involving node '{dep}'")
            in_stack.discard(node_id)

        for node_id in ids:
            if node_id not in visited:
                dfs(node_id)

    def ready_nodes(self, completed: set[str]) -> list[RoutingNode]:
        """Return nodes whose dependencies are all complete."""
        return [
            n
            for n in self.nodes
            if n.id not in completed and all(dep in completed for dep in n.depends_on)
        ]

    def is_complete(self, completed: set[str]) -> bool:
        """Return True if all nodes are complete."""
        return all(n.id in completed for n in self.nodes)


# --- Work brief (full plan artifact) ---


class WorkBriefNode(BaseModel):
    """Full execution specification for a single agent invocation.

    Mirrors RoutingNode with task content added. The stored
    artifact is a serialised WorkBrief. Workers resolve it via
    the inject_plan_context hook.
    """

    id: str
    depends_on: list[str] = []
    agent_id: str
    command: str
    task: str


class WorkBrief(BaseModel):
    """Full execution plan. Written to artifact store by planner.

    Never read by orchestrator. Resolved on worker side by
    inject_plan_context hook.
    """

    goal: str
    nodes: list[WorkBriefNode]
    assumptions: list[str] = []

    @model_validator(mode="after")
    def validate_graph(self) -> WorkBrief:
        """Validate DAG: non-empty, unique ids, no dangling deps."""
        if not self.nodes:
            raise ValueError("Work brief must contain at least one node")
        ids = {n.id for n in self.nodes}
        seen_ids: set[str] = set()
        for node in self.nodes:
            if node.id in seen_ids:
                raise ValueError(f"Duplicate node id '{node.id}'")
            seen_ids.add(node.id)
            for dep in node.depends_on:
                if dep not in ids:
                    raise ValueError(
                        f"Node '{node.id}' depends on unknown node '{dep}'"
                    )
        return self

    def to_routing_skeleton(self) -> RoutingSkeleton:
        """Project to routing skeleton — strips task content."""
        return RoutingSkeleton(
            goal=self.goal,
            nodes=[
                RoutingNode(
                    id=n.id,
                    depends_on=n.depends_on,
                    agent_id=n.agent_id,
                    command=n.command,
                )
                for n in self.nodes
            ],
        )


# --- Public compound-graph state ---


class RunState(TypedDict, total=False):
    """Slim public state schema for the compound default pipeline.

    Used as the parent state for ``build_default_graph`` — the
    top-level graph that composes ``entry`` / ``planning`` /
    ``execution`` subgraphs as nodes. Keys that phase subgraphs need
    (``task``, ``triage``, ``work_brief_pointer``,
    ``routing_skeleton``, …) flow through name-matching; fields only
    the parent cares about pass through untouched, so user code can
    extend via ``MyRunState(RunState, total=False)`` + extra keys and
    add custom nodes around the built-in subgraphs.

    Contract stability: additions are non-breaking (``total=False``);
    removals or renames ship with a major version bump. See
    ``docs/api/state.md`` for the versioning policy and the extension
    pattern. Internal phase state (``EntryState``, ``PlanningState``,
    ``ExecutionState``) stays private to the subgraph modules.
    """

    task: str
    run_id: str
    trace_id: str
    triage: dict[str, Any] | None
    work_brief_pointer: ArtifactPointer | None
    routing_skeleton: dict[str, Any] | None
    wave_results: Annotated[list[dict[str, Any]], _append_reducer]
    abort_reason: str | None


# --- Three-graph supervisor topology state schemas ---


class EntryState(TypedDict, total=False):
    """State for the entry/triage graph."""

    task: str
    triage: dict[str, Any] | None
    trace_id: str
    run_id: str


class PlanningState(TypedDict, total=False):
    """State for the planning graph with HITL approval loop."""

    task: str
    work_brief: dict[str, Any] | None  # legacy — replaced by pointer + skeleton
    work_brief_pointer: ArtifactPointer | None
    routing_skeleton: dict[str, Any] | None  # RoutingSkeleton.model_dump()
    planner_error: str | None
    planning_context: Annotated[list[dict[str, Any]], _append_reducer]
    human_feedback: str | None
    plan_approved: bool | None
    revision_count: int
    trace_id: str
    run_id: str


class SignalsSummary(TypedDict, total=False):
    """Typed summary of signal routing state for the current wave."""

    route_action: str | None
    wave_item_count: int


class ExecutionState(TypedDict, total=False):
    """State for the flat-DAG execution graph.

    ``routing_skeleton`` drives traversal; ``work_brief_pointer`` is
    passed to agents so the worker-side inject_plan_context hook can
    resolve task content. The orchestrator never reads the full brief.

    ``wave_results`` is retained as the append-only stream of per-node
    results, kept for client event continuity (one item per node, shape
    compatible with the old WaveResult).
    """

    work_brief_pointer: ArtifactPointer
    routing_skeleton: dict[str, Any]  # RoutingSkeleton.model_dump()
    completed_node_ids: list[str]
    wave_results: Annotated[list[dict[str, Any]], _append_reducer]
    wave_reflections: Annotated[list[dict[str, Any]], _append_reducer]
    signals: SignalsSummary | None
    abort_reason: str | None
    trace_id: str
    run_id: str
    # W3C trace context carrier (traceparent/tracestate) stashed by
    # initialise_execution so agent_node can re-attach it and make
    # every agent span a child of the execution root span.
    trace_carrier: dict[str, str]


class WaveItem(TypedDict, total=False):
    """A single work item dispatched via Send to agent_node.

    ``context`` carries resolved upstream outputs so each agent can see what
    prior waves produced. The orchestrator builds it in ``dispatch_wave``;
    individual agents receive it via the standard ``context`` parameter.

    ``trace_carrier`` carries the W3C trace context from the execution
    graph's root span so agent_node can re-attach it and make every
    agent span part of a single Langfuse trace.
    """

    agent_id: str
    command: str
    task: str
    phase_index: int
    wave_index: int
    item_index: int
    trace_id: str
    run_id: str
    context: list[dict[str, Any]]
    trace_carrier: dict[str, str]


class WaveResult(TypedDict):
    """Result from a single agent invocation within a wave.

    ``output`` and ``artifacts`` are distinct fields. ``output`` is the
    inline result (string or structured dict). ``artifacts`` lists the
    artifact pointers written by the agent. The orchestrator reads
    them as separate concerns — no fallback between them.
    """

    phase_index: int
    wave_index: int
    item_index: int
    agent_id: str
    command: str
    output: str | dict[str, Any] | None
    artifacts: list[dict[str, Any]]
    signals: list[dict[str, Any]]
