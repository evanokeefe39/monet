"""FastAPI server hosting agents over HTTP for the transport spike.

This is used to test that invoke_agent() produces identical results
when calling via HTTP vs direct function call.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .decorator import get_handler
from .models import AgentRunContext

app = FastAPI()


class InvokeRequest(BaseModel):
    task: str
    command: str = "fast"
    effort: str | None = None
    trace_id: str = ""
    run_id: str = ""


@app.post("/agents/{agent_id}/{command}")
async def invoke(agent_id: str, command: str, request: InvokeRequest) -> dict[str, Any]:
    handler = get_handler(agent_id, command)
    if handler is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' command '{command}' not found",
        )

    ctx = AgentRunContext(
        task=request.task,
        command=request.command,
        effort=request.effort,
        trace_id=request.trace_id,
        run_id=request.run_id,
        agent_id=agent_id,
    )
    result = await handler(ctx)
    return dataclasses.asdict(result)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
