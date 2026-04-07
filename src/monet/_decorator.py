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
import inspect
from typing import TYPE_CHECKING, Any, overload

from ._catalogue import _artifact_collector, get_catalogue
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
    content_limit: int = DEFAULT_CONTENT_LIMIT,
) -> AgentResult:
    """Assemble a successful AgentResult from a function's return value.

    If the output exceeds content_limit and a catalogue client is
    configured, automatically offloads to the catalogue and returns
    a pointer instead.
    """
    output_str = str(return_value)
    output: str | ArtifactPointer = output_str

    # Automatic content offload
    if len(output_str) > content_limit:
        from ._catalogue import _catalogue_backend

        if _catalogue_backend is not None:
            try:
                # CatalogueHandle.write() appends to _artifact_collector
                # which is the same list as `artifacts` (set by the decorator)
                pointer = await get_catalogue().write(
                    content=output_str.encode(),
                    content_type="text/plain",
                    summary=output_str[:200],
                    confidence=0.0,
                    completeness="complete",
                )
                output = pointer
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
def agent(fn: Callable[..., Any]) -> Callable[..., Any]: ...


@overload
def agent(
    *,
    agent_id: str,
    command: str = "fast",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...


def agent(
    fn: Callable[..., Any] | None = None,
    *,
    agent_id: str = "",
    command: str = "fast",
) -> Any:
    """Decorator that wraps a callable as an agent handler.

    Usage:
        @agent(agent_id="researcher")
        async def researcher(task: str) -> str: ...

        @agent(agent_id="writer", command="deep")
        async def writer_deep(task: str, context: list) -> str: ...

    Preconditions:
        All function parameters must be valid AgentRunContext field names.
        agent_id must be provided.
    Postconditions:
        The function is registered in the default registry.
        When called with an AgentRunContext, returns an AgentResult.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if not agent_id:
            msg = "agent_id is required for @agent decorator"
            raise ValueError(msg)

        _validate_signature(fn, agent_id)

        @functools.wraps(fn)
        async def wrapper(ctx: AgentRunContext) -> AgentResult:
            artifacts: list[ArtifactPointer] = []
            signal_list: list[Signal] = []
            tracer = get_tracer("monet.agent")
            ctx_token = _agent_context.set(ctx)
            sig_token = _signal_collector.set(signal_list)
            art_token = _artifact_collector.set(artifacts)
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
                            result, ctx, artifacts, signal_list
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
                _artifact_collector.reset(art_token)
                _signal_collector.reset(sig_token)
                _agent_context.reset(ctx_token)

        # Register in the default registry
        default_registry.register(agent_id, command, wrapper)

        # Attach metadata for introspection
        wrapper._agent_id = agent_id  # type: ignore[attr-defined]
        wrapper._command = command  # type: ignore[attr-defined]

        return wrapper

    if fn is not None:
        # Called without arguments — not supported, agent_id is required
        msg = "agent_id is required: use @agent(agent_id='...') not @agent"
        raise TypeError(msg)

    return decorator
