"""Agent invocation routes.

POST /agents/{agent_id}/{command} — input envelope in, output envelope out.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from monet._registry import default_registry
from monet._types import AgentRunContext

router = APIRouter()


class InvokeRequest(BaseModel):
    """Input envelope for agent invocation."""

    task: str
    command: str = "fast"
    effort: str | None = None
    trace_id: str = ""
    run_id: str = ""


@router.post("/{agent_id}/{command}")
async def invoke_agent(
    agent_id: str, command: str, request: InvokeRequest
) -> dict[str, Any]:
    """Invoke an agent by ID and command.

    Looks up the handler from the SDK registry, constructs
    AgentRunContext from the request body, calls the handler,
    and returns the output envelope.
    """
    handler = default_registry.lookup(agent_id, command)
    if handler is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' command '{command}' not found",
        )

    ctx = AgentRunContext(
        task=request.task,
        command=command,
        effort=request.effort,  # type: ignore[arg-type]
        trace_id=request.trace_id,
        run_id=request.run_id,
        agent_id=agent_id,
    )
    result = await handler(ctx)
    return dataclasses.asdict(result)
