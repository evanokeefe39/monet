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
from ._types import (
    AgentResult,
    AgentRunContext,
    AgentSignals,
    ArtifactPointer,
    SemanticErrorInfo,
)
from .exceptions import EscalationRequired, NeedsHumanReview, SemanticError

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
) -> AgentResult:
    """Assemble a successful AgentResult from a function's return value."""
    return AgentResult(
        success=True,
        output=str(return_value),
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
            token = _agent_context.set(ctx)
            try:
                kwargs = _inject_params(fn, ctx)
                if asyncio.iscoroutinefunction(fn):
                    result = await fn(**kwargs)
                else:
                    result = fn(**kwargs)
                return _wrap_result(result, ctx, artifacts)
            except Exception as exc:
                return _handle_exception(exc, ctx, artifacts)
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
