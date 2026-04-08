"""The @agent decorator — wraps any callable as an agent handler.

Composed internally from discrete functions:
- _validate_signature: fail fast on invalid parameter names
- _inject_params: build kwargs from context fields
- _wrap_result: assemble AgentResult from return value
- _handle_exception: typed exception -> signals translation

The public API is a single decorator. The internal implementation
separates concerns to keep each testable and replaceable independently.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
from typing import TYPE_CHECKING, Any, overload

from ._catalogue import _artifact_collector, _artifact_hashes, get_catalogue
from ._context import _agent_context
from ._registry import default_registry
from ._stubs import _signal_collector
from ._tracing import get_tracer
from .exceptions import EscalationRequired, NeedsHumanReview, SemanticError
from .types import (
    AgentResult,
    AgentRunContext,
    ArtifactPointer,
    Signal,
    SignalType,
)

# Default content limit for automatic offload (bytes)
DEFAULT_CONTENT_LIMIT = 4000

if TYPE_CHECKING:
    from collections.abc import Callable


# Fields available for injection from AgentRunContext
_CONTEXT_FIELDS: frozenset[str] = frozenset(AgentRunContext.__annotations__.keys())


def _validate_signature(fn: Callable[..., Any], agent_id: str) -> None:
    """Validate function parameters against AgentRunContext fields.

    Raises TypeError at decoration time (not call time) if any parameter
    name is not a valid AgentRunContext field.
    """
    sig = inspect.signature(fn)
    for param_name in sig.parameters:
        if param_name not in _CONTEXT_FIELDS:
            msg = (
                f"Parameter '{param_name}' on {fn.__qualname__} "
                f"(agent_id='{agent_id}') is not a valid AgentRunContext "
                f"field. Valid fields: {sorted(_CONTEXT_FIELDS)}"
            )
            raise TypeError(msg)


def _inject_params(fn: Callable[..., Any], ctx: AgentRunContext) -> dict[str, Any]:
    """Build kwargs from context fields matching function parameters."""
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    for param_name in sig.parameters:
        kwargs[param_name] = ctx[param_name]
    return kwargs


async def _wrap_result(
    return_value: Any,
    ctx: AgentRunContext,
    artifacts: list[ArtifactPointer],
    signals: list[Signal],
    written_hashes: set[str],
    content_limit: int = DEFAULT_CONTENT_LIMIT,
) -> AgentResult:
    """Assemble a successful AgentResult from a function's return value.

    Inline output is ``str | dict | None``. If a string return exceeds
    ``content_limit`` and a catalogue backend is configured, the full
    content is offloaded to the catalogue (the pointer lands in
    ``artifacts``) and ``output`` becomes a short inline summary.
    """
    output: str | dict[str, Any] | None
    if return_value is None:
        output = None
    elif isinstance(return_value, dict):
        output = return_value
    else:
        output_str = str(return_value)
        output = output_str
        if len(output_str) > content_limit:
            from ._catalogue import _catalogue_backend

            encoded = output_str.encode()
            already_written = hashlib.sha256(encoded).hexdigest() in written_hashes
            if already_written:
                # Agent already persisted exact bytes explicitly — suppress
                # the auto-offload, but still inline-summarise so consumers
                # see a compact output field matching the explicit artifact.
                output = output_str[:200]
            elif _catalogue_backend is not None:
                try:
                    # write() appends to _artifact_collector (same list as `artifacts`)
                    await get_catalogue().write(
                        content=output_str.encode(),
                        content_type="text/plain",
                        summary=output_str[:200],
                        confidence=0.0,
                        completeness="complete",
                    )
                    output = output_str[:200]
                except NotImplementedError:
                    pass

    return AgentResult(
        success=True,
        output=output,
        artifacts=list(artifacts),
        signals=list(signals),
        trace_id=ctx.get("trace_id", ""),
        run_id=ctx.get("run_id", ""),
    )


def _handle_exception(
    exc: Exception,
    ctx: AgentRunContext,
    artifacts: list[ArtifactPointer],
    signals: list[Signal],
) -> AgentResult:
    """Translate typed or unexpected exceptions into AgentResult with signals.

    Appends an appropriate signal to the accumulated list, then builds
    a failed AgentResult.
    """
    if isinstance(exc, NeedsHumanReview):
        signals.append(
            Signal(
                type=SignalType.NEEDS_HUMAN_REVIEW,
                reason=exc.reason,
                metadata=None,
            )
        )
    elif isinstance(exc, EscalationRequired):
        signals.append(
            Signal(
                type=SignalType.ESCALATION_REQUIRED,
                reason=exc.reason,
                metadata=None,
            )
        )
    elif isinstance(exc, SemanticError):
        signals.append(
            Signal(
                type=SignalType.SEMANTIC_ERROR,
                reason=exc.message,
                metadata={"error_type": exc.type},
            )
        )
    else:
        signals.append(
            Signal(
                type=SignalType.SEMANTIC_ERROR,
                reason=str(exc),
                metadata={"error_type": "unexpected_error"},
            )
        )

    return AgentResult(
        success=False,
        output="",
        artifacts=list(artifacts),
        signals=list(signals),
        trace_id=ctx.get("trace_id", ""),
        run_id=ctx.get("run_id", ""),
    )


@overload
def agent(
    agent_id_or_fn: str, /
) -> Callable[..., Callable[[Callable[..., Any]], Callable[..., Any]]]: ...


@overload
def agent(
    *,
    agent_id: str,
    command: str = "fast",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...


def agent(
    agent_id_or_fn: str | None = None,
    /,
    *,
    agent_id: str = "",
    command: str = "fast",
) -> Any:
    """Decorator that wraps a callable as an agent handler.

    Two call signatures:

    1. ``researcher = agent("researcher")`` — returns a decorator factory
       bound to ``agent_id``. Then ``@researcher(command="deep")`` registers
       a command handler.

    2. ``@agent(agent_id="researcher", command="deep")`` — verbose form.

    Both produce identical registry entries. Registration happens at
    decoration time (import time).
    """
    # Form 1: agent("researcher") → bound partial
    if isinstance(agent_id_or_fn, str):
        return functools.partial(agent, agent_id=agent_id_or_fn)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if not agent_id:
            msg = "agent_id is required for @agent decorator"
            raise ValueError(msg)

        _validate_signature(fn, agent_id)

        @functools.wraps(fn)
        async def wrapper(ctx: AgentRunContext) -> AgentResult:
            artifacts: list[ArtifactPointer] = []
            signal_list: list[Signal] = []
            written_hashes: set[str] = set()
            tracer = get_tracer("monet.agent")
            ctx_token = _agent_context.set(ctx)
            sig_token = _signal_collector.set(signal_list)
            art_token = _artifact_collector.set(artifacts)
            hash_token = _artifact_hashes.set(written_hashes)
            try:
                with tracer.start_as_current_span(
                    f"agent.{agent_id}.{command}",
                    attributes={
                        "agent.id": agent_id,
                        "agent.command": command,
                        "monet.run_id": ctx.get("run_id", ""),
                    },
                ) as span:
                    try:
                        kwargs = _inject_params(fn, ctx)
                        if asyncio.iscoroutinefunction(fn):
                            result = await fn(**kwargs)
                        else:
                            result = fn(**kwargs)
                        agent_result = await _wrap_result(
                            result, ctx, artifacts, signal_list, written_hashes
                        )
                        span.set_attribute("agent.success", agent_result.success)
                        return agent_result
                    except Exception as exc:
                        agent_result = _handle_exception(
                            exc, ctx, artifacts, signal_list
                        )
                        span.set_attribute("agent.success", False)
                        span.record_exception(exc)
                        return agent_result
            finally:
                _artifact_hashes.reset(hash_token)
                _artifact_collector.reset(art_token)
                _signal_collector.reset(sig_token)
                _agent_context.reset(ctx_token)

        # Register in the default registry
        default_registry.register(agent_id, command, wrapper)

        # Attach metadata for introspection
        wrapper._agent_id = agent_id  # type: ignore[attr-defined]
        wrapper._command = command  # type: ignore[attr-defined]

        return wrapper

    return decorator
