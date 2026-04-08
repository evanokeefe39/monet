"""Transport-agnostic agent invocation.

Dispatches to local function call or HTTP POST. Standard envelope fields
are explicit parameters; agent-specific parameters pass as **kwargs.
"""

from __future__ import annotations

import os
import secrets
import uuid
from typing import Any

from opentelemetry import propagate, trace

from monet._registry import default_registry
from monet.types import AgentResult, AgentRunContext, Signal

_RESERVED_FIELDS = {"task", "context", "command", "trace_id", "run_id", "skills"}

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


def _generate_trace_id() -> str:
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def extract_carrier_from_config(config: dict[str, Any] | None) -> dict[str, str]:
    """Pull the CLI-side trace carrier out of langgraph run metadata.

    The CLI injects a W3C traceparent carrier into each langgraph run's
    metadata under the ``monet_trace_carrier`` key. Graph entry nodes
    read it via their ``config`` argument and re-attach the trace
    context so downstream agent spans become part of the CLI-side root
    trace instead of each starting a new root. Returns ``{}`` when no
    carrier is present so callers can gate the attach step with a
    truthiness check.
    """
    if not config:
        return {}
    metadata = config.get("metadata") or {}
    carrier = metadata.get("monet_trace_carrier")
    return dict(carrier) if isinstance(carrier, dict) else {}


async def invoke_agent(
    agent_id: str,
    command: str = "fast",
    task: str = "",
    context: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    skills: list[str] | None = None,
    **kwargs: Any,
) -> AgentResult:
    """Invoke an agent by ID and command.

    Standard envelope fields are explicit parameters. Agent-specific
    parameters pass as **kwargs but must not shadow reserved fields.
    Routing is always driven by AgentResult.signals, never by kwargs values.
    """
    conflicts = _RESERVED_FIELDS & set(kwargs)
    if conflicts:
        msg = (
            f"invoke_agent() kwargs conflict with reserved fields: {conflicts}. "
            "Pass these as explicit parameters."
        )
        raise ValueError(msg)

    resolved_run_id = run_id or str(uuid.uuid4())
    resolved_trace_id = trace_id or _generate_trace_id()

    ctx: AgentRunContext = {
        "task": task,
        "context": context or [],
        "command": command,
        "trace_id": resolved_trace_id,
        "run_id": resolved_run_id,
        "agent_id": agent_id,
        "skills": skills or [],
    }

    tracer = trace.get_tracer("monet.orchestration")
    with tracer.start_as_current_span(
        f"agent.{agent_id}.{command}",
        attributes={
            "agent.id": agent_id,
            "agent.command": command,
            "monet.run_id": resolved_run_id,
        },
    ) as span:
        if get_transport_mode() == TRANSPORT_HTTP:
            endpoint = get_agent_endpoint(agent_id, command)
            if endpoint is not None:
                result = await _invoke_http(endpoint, ctx)
                span.set_attribute("agent.success", result.success)
                return result

        # Local invocation
        wrapper = default_registry.lookup(agent_id, command)
        if wrapper is None:
            msg = f"No handler for agent_id='{agent_id}', command='{command}'"
            raise LookupError(msg)
        result = await wrapper(ctx)
        span.set_attribute("agent.success", result.success)
        span.set_attribute("agent.signal_count", len(result.signals))
        return result


async def _invoke_http(endpoint: str, ctx: AgentRunContext) -> AgentResult:
    """Call an agent over HTTP POST.

    Uses opentelemetry.propagate.inject() for correct OTel context propagation.
    """
    import httpx

    payload: dict[str, Any] = {
        "task": ctx["task"],
        "command": ctx["command"],
        "trace_id": ctx["trace_id"],
        "run_id": ctx["run_id"],
    }
    headers: dict[str, str] = {}
    propagate.inject(headers)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

    raw_signals = data.get("signals", [])
    signals: list[Signal] = [
        Signal(
            type=s.get("type", ""),
            reason=s.get("reason", ""),
            metadata=s.get("metadata"),
        )
        for s in raw_signals
    ]

    return AgentResult(
        success=data["success"],
        output=data["output"],
        signals=signals,
        trace_id=data.get("trace_id", ""),
        run_id=data.get("run_id", ""),
    )
