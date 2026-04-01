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

from ._context import _agent_context
from ._registry import default_registry
from ._stubs import get_catalogue_client
from ._tracing import end_span, start_agent_span
from ._types import (
    AgentResult,
    AgentRunContext,
    AgentSignals,
    ArtifactPointer,
    SemanticErrorInfo,
)
from .exceptions import EscalationRequired, NeedsHumanReview, SemanticError

# Default content limit for automatic offload (bytes)
DEFAULT_CONTENT_LIMIT = 4000

if TYPE_CHECKING:
    from collections.abc import Callable


# Fields available for injection from AgentRunContext
_CONTEXT_FIELDS: frozenset[str] = frozenset(AgentRunContext.__dataclass_fields__.keys())


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
        kwargs[param_name] = getattr(ctx, param_name)
    return kwargs


def _wrap_result(
    return_value: Any,
    ctx: AgentRunContext,
    artifacts: list[ArtifactPointer],
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
        client = get_catalogue_client()
        if client is not None:
            from .catalogue._metadata import ArtifactMetadata

            metadata = ArtifactMetadata(
                content_type="text/plain",
                summary=output_str[:200],
                created_by=ctx.agent_id or "unknown",
                trace_id=ctx.trace_id,
                run_id=ctx.run_id,
                invocation_command=ctx.command,
            )
            pointer = client.write(output_str.encode(), metadata)
            artifacts = [*artifacts, pointer]
            output = pointer

    return AgentResult(
        success=True,
        output=output,
        artifacts=list(artifacts),
        signals=AgentSignals(),
        trace_id=ctx.trace_id,
        run_id=ctx.run_id,
    )


def _handle_exception(
    exc: Exception,
    ctx: AgentRunContext,
    artifacts: list[ArtifactPointer],
) -> AgentResult:
    """Translate typed or unexpected exceptions into AgentResult with signals."""
    if isinstance(exc, NeedsHumanReview):
        signals = AgentSignals(
            needs_human_review=True,
            review_reason=exc.reason,
        )
    elif isinstance(exc, EscalationRequired):
        signals = AgentSignals(
            escalation_requested=True,
            escalation_reason=exc.reason,
        )
    elif isinstance(exc, SemanticError):
        signals = AgentSignals(
            semantic_error=SemanticErrorInfo(type=exc.type, message=exc.message),
        )
    else:
        # Unexpected exception — wrap as semantic error
        signals = AgentSignals(
            semantic_error=SemanticErrorInfo(type="unexpected_error", message=str(exc)),
        )

    return AgentResult(
        success=False,
        output="",
        artifacts=list(artifacts),
        signals=signals,
        trace_id=ctx.trace_id,
        run_id=ctx.run_id,
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
            span = start_agent_span(
                agent_id=agent_id,
                command=command,
                effort=ctx.effort,
                run_id=ctx.run_id,
                trace_id=ctx.trace_id,
            )
            token = _agent_context.set(ctx)
            try:
                kwargs = _inject_params(fn, ctx)
                if asyncio.iscoroutinefunction(fn):
                    result = await fn(**kwargs)
                else:
                    result = fn(**kwargs)
                agent_result = _wrap_result(result, ctx, artifacts)
                end_span(span, success=True)
                return agent_result
            except Exception as exc:
                agent_result = _handle_exception(exc, ctx, artifacts)
                end_span(span, success=False, error_message=str(exc))
                return agent_result
            finally:
                _agent_context.reset(token)

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
