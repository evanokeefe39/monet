"""invoke_agent() — the transport-agnostic agent caller.

This is the most load-bearing question from SPIKES.md Spike 1:
can invoke_agent() call the same agent as a direct Python function
and as an HTTP endpoint, switched by config, producing identical results?
"""

from __future__ import annotations

import httpx

from .models import (
    AgentDescriptor,
    AgentResult,
    AgentRunContext,
    AgentSignals,
    HttpDescriptor,
    InputEnvelope,
    LocalDescriptor,
)


async def invoke_agent(
    agent_id: str,
    command: str,
    envelope: InputEnvelope,
    descriptor: AgentDescriptor,
) -> AgentResult:
    """Call an agent via direct function or HTTP, based on descriptor type.

    The caller never branches on transport. The descriptor determines the path.
    """
    if isinstance(descriptor, LocalDescriptor):
        return await _invoke_local(envelope, descriptor)
    elif isinstance(descriptor, HttpDescriptor):
        return await _invoke_http(envelope, descriptor)
    else:
        msg = f"Unknown descriptor type: {type(descriptor)}"
        raise TypeError(msg)


async def _invoke_local(
    envelope: InputEnvelope,
    descriptor: LocalDescriptor,
) -> AgentResult:
    """Call a decorated Python function directly."""
    ctx = AgentRunContext(
        task=envelope.task,
        command=envelope.command,
        effort=envelope.effort,
        trace_id=envelope.trace_id,
        run_id=envelope.run_id,
        agent_id=descriptor.agent_id,
    )
    result: AgentResult = await descriptor.callable_ref(ctx)
    return result


async def _invoke_http(
    envelope: InputEnvelope,
    descriptor: HttpDescriptor,
) -> AgentResult:
    """Call an agent over HTTP POST."""
    payload = {
        "task": envelope.task,
        "command": envelope.command,
        "effort": envelope.effort,
        "trace_id": envelope.trace_id,
        "run_id": envelope.run_id,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            descriptor.endpoint,
            json=payload,
            headers={"traceparent": envelope.trace_id},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

    return AgentResult(
        success=data["success"],
        output=data["output"],
        signals=AgentSignals(
            needs_human_review=data.get("signals", {}).get("needs_human_review", False),
            review_reason=data.get("signals", {}).get("review_reason"),
            escalation_requested=data.get("signals", {}).get(
                "escalation_requested", False
            ),
            escalation_reason=data.get("signals", {}).get("escalation_reason"),
            semantic_error=data.get("signals", {}).get("semantic_error"),
        ),
        trace_id=data.get("trace_id", ""),
        run_id=data.get("run_id", ""),
    )
