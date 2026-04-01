"""Ad hoc @agent decorator for the transport spike.

Minimal implementation — validates the context injection and result
wrapping patterns before the real SDK is built.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from .models import (
    AgentResult,
    AgentRunContext,
    AgentSignals,
    _agent_context,
)

# Module-level registry: (agent_id, command) -> callable
_registry: dict[tuple[str, str], Callable[..., Any]] = {}


class NeedsHumanReview(Exception):  # noqa: N818
    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(reason)


class SemanticError(Exception):
    def __init__(self, type: str = "unknown", message: str = "") -> None:
        self.type = type
        self.message = message
        super().__init__(message)


def agent(agent_id: str, command: str = "fast") -> Callable[..., Any]:
    """Decorator that wraps a callable as an agent handler."""

    def wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
        # Validate signature at decoration time
        sig = inspect.signature(fn)
        valid_fields = set(AgentRunContext.__dataclass_fields__)
        for param_name in sig.parameters:
            if param_name not in valid_fields:
                msg = (
                    f"Parameter '{param_name}' on {fn.__name__} is not a "
                    f"valid AgentRunContext field. Valid: {sorted(valid_fields)}"
                )
                raise TypeError(msg)

        @functools.wraps(fn)
        async def inner(ctx: AgentRunContext) -> AgentResult:
            token = _agent_context.set(ctx)
            try:
                # Inject only declared params
                kwargs: dict[str, Any] = {}
                for param_name in sig.parameters:
                    kwargs[param_name] = getattr(ctx, param_name)

                if asyncio.iscoroutinefunction(fn):
                    result = await fn(**kwargs)
                else:
                    result = fn(**kwargs)

                return AgentResult(
                    success=True,
                    output=str(result),
                    trace_id=ctx.trace_id,
                    run_id=ctx.run_id,
                )
            except NeedsHumanReview as e:
                return AgentResult(
                    success=False,
                    output="",
                    signals=AgentSignals(
                        needs_human_review=True,
                        review_reason=e.reason,
                    ),
                    trace_id=ctx.trace_id,
                    run_id=ctx.run_id,
                )
            except SemanticError as e:
                return AgentResult(
                    success=False,
                    output="",
                    signals=AgentSignals(
                        semantic_error={"type": e.type, "message": e.message},
                    ),
                    trace_id=ctx.trace_id,
                    run_id=ctx.run_id,
                )
            except Exception as e:
                return AgentResult(
                    success=False,
                    output="",
                    signals=AgentSignals(
                        semantic_error={
                            "type": "unexpected_error",
                            "message": str(e),
                        },
                    ),
                    trace_id=ctx.trace_id,
                    run_id=ctx.run_id,
                )
            finally:
                _agent_context.reset(token)

        # Register
        _registry[(agent_id, command)] = inner
        inner._agent_id = agent_id  # type: ignore[attr-defined]
        inner._command = command  # type: ignore[attr-defined]
        return inner

    return wrapper


def get_handler(agent_id: str, command: str = "fast") -> Callable[..., Any] | None:
    return _registry.get((agent_id, command))


def clear_registry() -> None:
    _registry.clear()
