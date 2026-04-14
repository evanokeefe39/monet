"""Worker-side hook — resolve work brief pointer and inject plan node context.

Registered as a ``before_agent`` hook. When the agent's context contains
a ``plan_item`` entry written by the execution graph, this hook fetches
the work brief artifact, locates the node by id, and injects the node's
task as the agent's task. The full brief goal is included as context.

For agents invoked outside the execution graph (no ``plan_item`` entry),
the context is returned unchanged.

Shape validation via ``WorkBrief.model_validate()`` at resolution time
is the second validation gate — the planner validated at write time, so
if the artifact shape has drifted, this fails fast before the agent runs.
"""

from __future__ import annotations

import json

from monet import AgentMeta, AgentRunContext, get_artifacts, on_hook
from monet.orchestration._state import WorkBrief

__all__ = ["inject_plan_context"]


@on_hook("before_agent")
async def inject_plan_context(ctx: AgentRunContext, meta: AgentMeta) -> AgentRunContext:
    """Resolve work brief and inject the relevant plan node."""
    plan_entry = next(
        (e for e in ctx["context"] if e.get("type") == "plan_item"),
        None,
    )
    if plan_entry is None:
        return ctx

    pointer = plan_entry["work_brief_pointer"]
    node_id = plan_entry["node_id"]

    content_bytes, _meta = await get_artifacts().read(pointer["artifact_id"])
    work_brief = WorkBrief.model_validate(json.loads(content_bytes.decode()))

    node = next((n for n in work_brief.nodes if n.id == node_id), None)
    if node is None:
        raise ValueError(
            f"Node '{node_id}' not found in work brief "
            f"'{pointer['artifact_id']}'. "
            f"Available nodes: {[n.id for n in work_brief.nodes]}"
        )

    enriched_context = [
        {"type": "plan_goal", "content": work_brief.goal},
        *[e for e in ctx["context"] if e.get("type") != "plan_item"],
    ]

    return AgentRunContext(
        task=node.task,
        context=enriched_context,
        command=ctx["command"],
        trace_id=ctx["trace_id"],
        run_id=ctx["run_id"],
        agent_id=ctx["agent_id"],
        skills=ctx["skills"],
    )
