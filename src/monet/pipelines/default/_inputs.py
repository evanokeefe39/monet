"""Initial-state builders for the default pipeline's planning and
execution graphs.

The entry graph uses the generic :func:`monet.client._wire.task_input`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from monet.types import ArtifactPointer


def planning_input(task: str, run_id: str) -> dict[str, Any]:
    """Build the initial state dict for the planning graph."""
    return {
        "task": task,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
        "revision_count": 0,
    }


def execution_input(
    work_brief_pointer: ArtifactPointer,
    routing_skeleton: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Build the initial state dict for the execution graph.

    ``work_brief_pointer`` is threaded to each agent invocation so the
    worker-side ``inject_plan_context`` hook can resolve task content.
    ``routing_skeleton`` is the flat DAG (``{goal, nodes}``) that drives
    traversal.
    """
    return {
        "work_brief_pointer": work_brief_pointer,
        "routing_skeleton": routing_skeleton,
        "completed_node_ids": [],
        "wave_results": [],
        "wave_reflections": [],
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
    }
