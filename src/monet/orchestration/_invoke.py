"""Transport-agnostic agent invocation.

Dispatches to local function call or HTTP POST. Standard envelope fields
are explicit parameters; agent-specific parameters pass as **kwargs.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import uuid
from typing import Any

from opentelemetry import propagate, trace

from monet._registry import default_registry
from monet.signals import SignalType
from monet.types import AgentResult, AgentRunContext, ArtifactPointer, Signal

# HTTP transport timeout (seconds). Default 300s is generous because
# agents doing deep research, long-form writing, or multi-turn tool
# use routinely exceed 30s. Override via MONET_HTTP_TIMEOUT.
_DEFAULT_HTTP_TIMEOUT = 300.0

# Local invocation timeout (seconds). Protects against runaway agents
# that never return. Override via MONET_LOCAL_TIMEOUT. Default 600s
# (10 minutes) is generous for long-running research agents.
_DEFAULT_LOCAL_TIMEOUT = 600.0


def _get_http_timeout() -> float:
    raw = os.environ.get("MONET_HTTP_TIMEOUT")
    if not raw:
        return _DEFAULT_HTTP_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_HTTP_TIMEOUT


def _get_local_timeout() -> float:
    raw = os.environ.get("MONET_LOCAL_TIMEOUT")
    if not raw:
        return _DEFAULT_LOCAL_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_LOCAL_TIMEOUT


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
        try:
            result = await asyncio.wait_for(wrapper(ctx), timeout=_get_local_timeout())
        except TimeoutError:
            timeout_signal = Signal(
                type=SignalType.SEMANTIC_ERROR,
                reason=f"Agent timed out after {_get_local_timeout()}s",
                metadata={"error_type": "timeout"},
            )
            result = AgentResult(
                success=False,
                output="",
                signals=(timeout_signal,),
                trace_id=resolved_trace_id,
                run_id=resolved_run_id,
            )
        span.set_attribute("agent.success", result.success)
        span.set_attribute("agent.signal_count", len(result.signals))
        return result


# Module-level HTTP client for connection pooling across invocations.
# Lazy-initialized on first HTTP call; avoids import-time httpx dependency
# for local-only deployments.
_http_client: Any = None


def _get_http_client() -> Any:
    """Return a shared httpx.AsyncClient, creating it on first use."""
    global _http_client
    if _http_client is None:
        import httpx

        _http_client = httpx.AsyncClient(timeout=_get_http_timeout())
    return _http_client


async def _invoke_http(endpoint: str, ctx: AgentRunContext) -> AgentResult:
    """Call an agent over HTTP POST.

    Uses opentelemetry.propagate.inject() for correct OTel context
    propagation. Timeout defaults to 300s and can be overridden via
    ``MONET_HTTP_TIMEOUT``. Artifacts in the response are deserialized
    back to ``ArtifactPointer`` so catalogue-writing agents transported
    over HTTP retain their pointers on the receiving side.

    Uses a module-level httpx.AsyncClient for connection pooling instead
    of creating a new client per request.
    """
    payload: dict[str, Any] = {
        "task": ctx["task"],
        "command": ctx["command"],
        "trace_id": ctx["trace_id"],
        "run_id": ctx["run_id"],
    }
    headers: dict[str, str] = {}
    propagate.inject(headers)

    client = _get_http_client()
    response = await client.post(
        endpoint,
        json=payload,
        headers=headers,
    )
    response.raise_for_status()
    data = response.json()

    raw_signals = data.get("signals", [])
    signals = tuple(
        Signal(
            type=s.get("type", ""),
            reason=s.get("reason", ""),
            metadata=s.get("metadata"),
        )
        for s in raw_signals
    )

    raw_artifacts = data.get("artifacts", []) or []
    artifacts = tuple(
        ArtifactPointer(
            artifact_id=a.get("artifact_id", ""),
            url=a.get("url", ""),
        )
        for a in raw_artifacts
        if isinstance(a, dict) and a.get("artifact_id")
    )

    return AgentResult(
        success=data["success"],
        output=data["output"],
        artifacts=artifacts,
        signals=signals,
        trace_id=data.get("trace_id", ""),
        run_id=data.get("run_id", ""),
    )
