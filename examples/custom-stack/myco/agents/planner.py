"""Custom planner — bespoke shape, zero monet.agents reuse.

The chat and pipeline graphs in this example consume the planner's
output directly (no reliance on monet's ``_planner_outcome`` classifier
or ``work_brief`` artifact convention). This proves the coupling lives
between the *user's* planner and the *user's* graph — monet core does
not enforce a planner protocol.
"""

from __future__ import annotations

import json

from monet import agent, emit_progress, write_artifact

from ._stub_llm import canned_response


@agent(agent_id="myco_planner", command="plan", pool="local")
async def plan(task: str) -> dict[str, object]:
    """Produce a two-step plan and persist it as an artifact."""
    emit_progress({"agent": "myco_planner", "status": "building_plan"})
    body = canned_response(task, kind="plan")
    plan_doc = {
        "goal": task,
        "steps": [
            {"id": "research", "agent_id": "myco_researcher", "command": "gather"},
            {
                "id": "draft",
                "agent_id": "myco_writer",
                "command": "compose",
                "depends_on": ["research"],
            },
        ],
        "summary": body,
    }
    pointer = await write_artifact(
        content=json.dumps(plan_doc).encode("utf-8"),
        content_type="application/json",
        summary=f"plan: {task[:80]}",
        key="myco_plan",
    )
    return {"plan_id": pointer["artifact_id"], "summary": body}
