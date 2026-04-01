"""Transport-agnostic agent invocation.

Dispatches to local function call or HTTP POST based on descriptor type.
Validated by spike_transport — identical results from both paths.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from monet._registry import default_registry
from monet._types import (
    AgentResult,
    AgentRunContext,
    AgentSignals,
    SemanticErrorInfo,
)

# Transport mode from environment
TRANSPORT_LOCAL = "local"
TRANSPORT_HTTP = "http"


def get_transport_mode() -> str:
    """Read transport mode from MONET_AGENT_TRANSPORT env var."""
    return os.environ.get("MONET_AGENT_TRANSPORT", TRANSPORT_LOCAL)


def get_agent_endpoint(agent_id: str, command: str) -> str | None:
    """Read HTTP endpoint for an agent from environment.

    Convention: MONET_AGENT_{AGENT_ID}_URL (uppercased, hyphens to underscores).
    """
    env_key = f"MONET_AGENT_{agent_id.upper().replace('-', '_')}_URL"
    base_url = os.environ.get(env_key)
    if base_url:
        return f"{base_url.rstrip('/')}/agents/{agent_id}/{command}"
    return None


async def invoke_agent(
    agent_id: str,
    command: str,
    ctx: AgentRunContext,
) -> AgentResult:
    """Invoke an agent via local call or HTTP, based on transport config.

    Local mode (default): looks up handler in registry, calls directly.
    HTTP mode: POSTs to agent endpoint derived from environment.

    Falls back to local if HTTP endpoint is not configured for this agent.
    """
    mode = get_transport_mode()

    if mode == TRANSPORT_HTTP:
        endpoint = get_agent_endpoint(agent_id, command)
        if endpoint is not None:
            return await _invoke_http(endpoint, ctx)

    # Local invocation (default, or HTTP fallback)
    return await _invoke_local(agent_id, command, ctx)


async def _invoke_local(
    agent_id: str, command: str, ctx: AgentRunContext
) -> AgentResult:
    """Call a decorated Python function directly."""
    handler = default_registry.lookup(agent_id, command)
    if handler is None:
        msg = f"No handler for agent_id='{agent_id}', command='{command}'"
        raise LookupError(msg)
    result: AgentResult = await handler(ctx)
    return result


async def _invoke_http(
    endpoint: str,
    ctx: AgentRunContext,
) -> AgentResult:
    """Call an agent over HTTP POST."""
    payload: dict[str, Any] = {
        "task": ctx.task,
        "command": ctx.command,
        "effort": ctx.effort,
        "trace_id": ctx.trace_id,
        "run_id": ctx.run_id,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            endpoint,
            json=payload,
            headers={"traceparent": ctx.trace_id},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

    signals_data = data.get("signals", {})
    semantic_error = signals_data.get("semantic_error")
    semantic_error_info = None
    if semantic_error:
        semantic_error_info = SemanticErrorInfo(
            type=semantic_error.get("type", "unknown"),
            message=semantic_error.get("message", ""),
        )

    return AgentResult(
        success=data["success"],
        output=data["output"],
        signals=AgentSignals(
            needs_human_review=signals_data.get("needs_human_review", False),
            review_reason=signals_data.get("review_reason"),
            escalation_requested=signals_data.get("escalation_requested", False),
            escalation_reason=signals_data.get("escalation_reason"),
            semantic_error=semantic_error_info,
        ),
        trace_id=data.get("trace_id", ""),
        run_id=data.get("run_id", ""),
    )
