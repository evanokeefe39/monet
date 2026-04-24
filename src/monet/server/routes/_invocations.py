"""Direct agent invocation routes."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from monet.server._auth import require_api_key

router = APIRouter()


class InvokeAgentRequest(BaseModel):
    """Body for ``POST /api/v1/agents/{agent_id}/{command}/invoke``."""

    task: str = ""
    context: list[dict[str, Any]] | None = None
    skills: list[str] | None = None


@router.post(
    "/agents/{agent_id}/{command}/invoke",
    dependencies=[Depends(require_api_key)],
)
async def invoke_agent_endpoint(
    agent_id: str,
    command: str,
    body: InvokeAgentRequest,
) -> dict[str, Any]:
    """Run a single ``agent_id:command`` invocation and return the result."""
    from monet.orchestration import invoke_agent

    try:
        result = await invoke_agent(
            agent_id,
            command=command,
            task=body.task,
            context=body.context,
            skills=body.skills,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return cast("dict[str, Any]", result)
