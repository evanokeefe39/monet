"""The @agent decorator — returns an Agent declaration object.

Agent carries configuration (agent_id, command, pool, allow_empty) and
delegates all runtime behavior to enter_agent_run() in engine.py.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, overload

from .engine import _validate_signature
from .registry import default_registry

if TYPE_CHECKING:
    from collections.abc import Callable

    from monet.types import AgentResult, AgentRunContext


class Agent:
    """Declaration object produced by @agent.

    Carries configuration; delegates execution to enter_agent_run().
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        agent_id: str,
        command: str = "fast",
        pool: str = "local",
        allow_empty: bool = False,
    ) -> None:
        self.fn = fn
        self.agent_id = agent_id
        self.command = command
        self.pool = pool
        self.allow_empty = allow_empty
        functools.update_wrapper(self, fn)

    async def __call__(self, ctx: AgentRunContext) -> AgentResult:
        """Direct invocation — delegates to engine."""
        from .engine import enter_agent_run

        return await enter_agent_run(self, ctx)

    # Backwards-compat aliases — migrate call sites to .agent_id / .command / .pool
    @property
    def _agent_id(self) -> str:
        return self.agent_id

    @property
    def _command(self) -> str:
        return self.command

    @property
    def _pool(self) -> str:
        return self.pool


@overload
def agent(
    agent_id_or_fn: str, /
) -> Callable[..., Callable[[Callable[..., Any]], Agent]]: ...


@overload
def agent(
    *,
    agent_id: str,
    command: str = "fast",
    allow_empty: bool = False,
    pool: str = "local",
) -> Callable[[Callable[..., Any]], Agent]: ...


def agent(
    agent_id_or_fn: str | None = None,
    /,
    *,
    agent_id: str = "",
    command: str = "fast",
    allow_empty: bool = False,
    pool: str = "local",
) -> Any:
    """Decorator that wraps a callable as an agent handler.

    Two call signatures:

    1. ``researcher = agent("researcher")`` — returns a decorator factory
       bound to ``agent_id``. Then ``@researcher(command="deep")`` registers
       a command handler.

    2. ``@agent(agent_id="researcher", command="deep")`` — verbose form.

    Both produce identical registry entries. Registration happens at
    decoration time (import time).

    ``allow_empty`` (default ``False``) disables the empty-result
    poka-yoke in ``_wrap_result``. Only set to ``True`` for agents that
    legitimately return no output and write no artifacts, such as
    signal-only ack handlers.
    """
    # Form 1: agent("researcher") → bound partial
    if isinstance(agent_id_or_fn, str):
        return functools.partial(agent, agent_id=agent_id_or_fn)

    def decorator(fn: Callable[..., Any]) -> Agent:
        if not agent_id:
            msg = "agent_id is required for @agent decorator"
            raise ValueError(msg)

        _validate_signature(fn, agent_id)
        obj = Agent(
            fn,
            agent_id=agent_id,
            command=command,
            pool=pool,
            allow_empty=allow_empty,
        )
        default_registry.register(agent_id, command, obj)
        return obj

    return decorator
