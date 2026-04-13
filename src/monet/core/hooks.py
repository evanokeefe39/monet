"""Hook registries and decorators for lifecycle event hooks.

Two registries serve two process boundaries:

- ``HookRegistry`` (worker-side): ``before_agent`` and ``after_agent``
  hooks that fire inside the ``@agent`` decorator wrapper. Populated
  via ``@on_hook`` at import time, same pattern as ``@agent`` →
  ``default_registry``.

- ``GraphHookRegistry`` (server-side): arbitrary named hook points
  inside graph nodes. Passed explicitly to graph builder functions.
  No global state — the caller controls what is registered.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import logging
import threading
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast, overload

from opentelemetry import trace

from monet.exceptions import SemanticError
from monet.types import AgentMeta, AgentResult, AgentRunContext

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

logger = logging.getLogger("monet.hooks")
_tracer = trace.get_tracer("monet.hooks")

T = TypeVar("T")

# Protected fields that hooks cannot overwrite in AgentRunContext.
_PROTECTED_FIELDS: frozenset[str] = frozenset({"run_id", "trace_id", "agent_id"})

# Valid AgentRunContext keys for merge validation.
_CONTEXT_KEYS: frozenset[str] = frozenset(AgentRunContext.__annotations__.keys())

# Default timeouts for worker and graph hooks (seconds).
DEFAULT_WORKER_HOOK_TIMEOUT = 5.0
DEFAULT_GRAPH_HOOK_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Merge contract
# ---------------------------------------------------------------------------


def merge_context(
    original: AgentRunContext,
    modified: AgentRunContext | None,
) -> AgentRunContext:
    """Merge a hook's returned context on top of the original.

    Preconditions:
        ``original`` is a valid ``AgentRunContext``.

    Postconditions:
        - If ``modified`` is ``None``, returns ``original`` unchanged.
        - Unknown keys in ``modified`` raise ``SemanticError``.
        - Protected fields (``run_id``, ``trace_id``, ``agent_id``) are
          always restored from ``original``.
        - Result is a valid ``AgentRunContext``.
    """
    if modified is None:
        return original
    extra = set(modified.keys()) - _CONTEXT_KEYS
    if extra:
        raise SemanticError(
            type="invalid_hook_return",
            message=f"Hook returned unknown AgentRunContext keys: {sorted(extra)}",
        )
    result = {**original, **modified}
    for field in _PROTECTED_FIELDS:
        result[field] = original[field]  # type: ignore[literal-required]
    return cast("AgentRunContext", result)


# ---------------------------------------------------------------------------
# Worker hook registry
# ---------------------------------------------------------------------------


class _HookEntry:
    """Internal: a registered hook with its metadata."""

    __slots__ = ("event", "handler", "match", "priority", "timeout")

    def __init__(
        self,
        event: str,
        handler: Callable[..., Any],
        match: str,
        priority: int,
        timeout: float,
    ) -> None:
        self.event = event
        self.handler = handler
        self.match = match
        self.priority = priority
        self.timeout = timeout

    def matches(self, agent_id: str, command: str) -> bool:
        """Test whether this hook matches the given agent_id and command.

        Match patterns:
        - ``"*"`` matches everything.
        - ``"writer"`` matches agent_id ``"writer"`` with any command.
        - ``"writer(deep)"`` matches agent_id ``"writer"`` command ``"deep"``.
        - ``"writer|qa"`` matches either agent_id (pipe-separated).
        - Glob wildcards (``*``, ``?``) supported in each segment.
        """
        for pattern in self.match.split("|"):
            pattern = pattern.strip()
            if pattern == "*":
                return True
            if "(" in pattern and pattern.endswith(")"):
                # agent_id(command) form
                aid_pat, cmd_pat = pattern[:-1].split("(", 1)
                if fnmatch.fnmatch(agent_id, aid_pat) and fnmatch.fnmatch(
                    command, cmd_pat
                ):
                    return True
            elif fnmatch.fnmatch(agent_id, pattern):
                return True
        return False


class HookRegistry:
    """Registry for worker-side hooks (``before_agent``, ``after_agent``).

    Thread-safe. Hooks register at import time (synchronous), execute at
    call time (async).
    """

    def __init__(self) -> None:
        self._hooks: list[_HookEntry] = []
        self._lock = threading.Lock()

    def register(
        self,
        event: str,
        handler: Callable[..., Any],
        match: str = "*",
        priority: int = 0,
        timeout: float = DEFAULT_WORKER_HOOK_TIMEOUT,
    ) -> None:
        """Register a hook handler for an event.

        Args:
            event: ``"before_agent"`` or ``"after_agent"``.
            handler: Async callable receiving event-specific arguments.
            match: Agent matcher pattern (see ``_HookEntry.matches``).
            priority: Lower runs first. Same priority = registration order.
            timeout: Seconds before the hook is killed. Default 5s.
        """
        if event not in ("before_agent", "after_agent"):
            raise ValueError(
                f"Worker hook event must be 'before_agent' or 'after_agent', "
                f"got {event!r}"
            )
        entry = _HookEntry(event, handler, match, priority, timeout)
        with self._lock:
            self._hooks.append(entry)

    def lookup(self, event: str, agent_id: str, command: str) -> list[_HookEntry]:
        """Return matching hooks sorted by priority, then registration order."""
        with self._lock:
            hooks = list(self._hooks)
        matching = [
            h for h in hooks if h.event == event and h.matches(agent_id, command)
        ]
        # Stable sort by priority (registration order preserved within same priority)
        matching.sort(key=lambda h: h.priority)
        return matching

    def clear(self) -> None:
        """Remove all registrations."""
        with self._lock:
            self._hooks.clear()

    @contextlib.contextmanager
    def hook_scope(self) -> Generator[None]:
        """Snapshot and restore hook state for test isolation."""
        with self._lock:
            snapshot = list(self._hooks)
        try:
            yield
        finally:
            with self._lock:
                self._hooks = snapshot

    def registered_hooks(self) -> list[tuple[str, str, str]]:
        """Return all registered hooks as (event, match, handler_name) triples."""
        with self._lock:
            return [(h.event, h.match, h.handler.__qualname__) for h in self._hooks]


default_hook_registry = HookRegistry()


# ---------------------------------------------------------------------------
# run_worker_hooks: execute before_agent / after_agent chains
# ---------------------------------------------------------------------------


async def run_before_agent_hooks(
    ctx: AgentRunContext,
    agent_id: str,
    command: str,
    registry: HookRegistry | None = None,
) -> AgentRunContext:
    """Run all matching ``before_agent`` hooks sequentially.

    Each hook receives the (possibly modified) context from the previous
    hook. Returns the final merged context. Raises ``SemanticError`` on
    hook failure or timeout — the agent never runs.
    """
    reg = registry or default_hook_registry
    hooks = reg.lookup("before_agent", agent_id, command)
    if not hooks:
        return ctx

    meta = AgentMeta(agent_id=agent_id, command=command)
    current_ctx = ctx

    for entry in hooks:
        hook_name = entry.handler.__qualname__
        with _tracer.start_as_current_span(
            f"hook.before_agent.{hook_name}",
            attributes={
                "hook.event": "before_agent",
                "hook.handler": hook_name,
                "hook.match": entry.match,
                "hook.priority": entry.priority,
                "agent.id": agent_id,
                "agent.command": command,
            },
        ):
            try:
                result = await asyncio.wait_for(
                    entry.handler(current_ctx, meta),
                    timeout=entry.timeout,
                )
                current_ctx = merge_context(current_ctx, result)
            except TimeoutError as te:
                raise SemanticError(
                    type="hook_timeout",
                    message=(
                        f"before_agent hook {hook_name!r} timed out "
                        f"after {entry.timeout}s"
                    ),
                ) from te
            except SemanticError:
                raise
            except Exception as exc:
                raise SemanticError(
                    type="hook_error",
                    message=f"before_agent hook {hook_name!r} failed: {exc}",
                ) from exc

    return current_ctx


async def run_after_agent_hooks(
    result: AgentResult,
    agent_id: str,
    command: str,
    registry: HookRegistry | None = None,
) -> AgentResult:
    """Run all matching ``after_agent`` hooks sequentially.

    Each hook receives the (possibly modified) result from the previous
    hook. Returns the final result. Raises ``SemanticError`` on hook
    failure or timeout.
    """
    reg = registry or default_hook_registry
    hooks = reg.lookup("after_agent", agent_id, command)
    if not hooks:
        return result

    meta = AgentMeta(agent_id=agent_id, command=command)
    current_result = result

    for entry in hooks:
        hook_name = entry.handler.__qualname__
        with _tracer.start_as_current_span(
            f"hook.after_agent.{hook_name}",
            attributes={
                "hook.event": "after_agent",
                "hook.handler": hook_name,
                "hook.match": entry.match,
                "hook.priority": entry.priority,
                "agent.id": agent_id,
                "agent.command": command,
            },
        ):
            try:
                modified = await asyncio.wait_for(
                    entry.handler(current_result, meta),
                    timeout=entry.timeout,
                )
                if modified is not None:
                    if not isinstance(modified, AgentResult):
                        raise SemanticError(
                            type="invalid_hook_return",
                            message=(
                                f"after_agent hook {hook_name!r} must return "
                                f"AgentResult or None, got {type(modified).__name__}"
                            ),
                        )
                    current_result = modified
            except TimeoutError as te:
                raise SemanticError(
                    type="hook_timeout",
                    message=(
                        f"after_agent hook {hook_name!r} timed out "
                        f"after {entry.timeout}s"
                    ),
                ) from te
            except SemanticError:
                raise
            except Exception as exc:
                raise SemanticError(
                    type="hook_error",
                    message=f"after_agent hook {hook_name!r} failed: {exc}",
                ) from exc

    return current_result


# ---------------------------------------------------------------------------
# @on_hook decorator
# ---------------------------------------------------------------------------


@overload
def on_hook(
    event: str,
    *,
    match: str = "*",
    priority: int = 0,
    timeout: float | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...


@overload
def on_hook(
    event: str,
    *,
    match: str = "*",
    priority: int = 0,
    timeout: float | None = None,
    registry: HookRegistry,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...


def on_hook(
    event: str,
    *,
    match: str = "*",
    priority: int = 0,
    timeout: float | None = None,
    registry: HookRegistry | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that registers a worker hook at import time.

    Usage::

        @on_hook("before_agent", match="writer|qa", priority=10)
        async def inject_tone(
            ctx: AgentRunContext, meta: AgentMeta,
        ) -> AgentRunContext | None:
            ...

        @on_hook("after_agent", match="*")
        async def validate_output(
            result: AgentResult, meta: AgentMeta,
        ) -> AgentResult | None:
            ...

    Args:
        event: ``"before_agent"`` or ``"after_agent"``.
        match: Agent matcher pattern.
        priority: Lower runs first.
        timeout: Per-hook timeout in seconds. Defaults to 5s for worker hooks.
        registry: Target registry. Defaults to ``default_hook_registry``.
    """
    resolved_timeout = timeout if timeout is not None else DEFAULT_WORKER_HOOK_TIMEOUT
    reg = registry or default_hook_registry

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if not asyncio.iscoroutinefunction(fn):
            raise TypeError(f"Hook {fn.__qualname__!r} must be an async function")
        reg.register(
            event, fn, match=match, priority=priority, timeout=resolved_timeout
        )
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Graph hook registry
# ---------------------------------------------------------------------------


class _GraphHookEntry:
    """Internal: a registered graph hook."""

    __slots__ = ("event", "handler", "on_error", "timeout")

    def __init__(
        self,
        event: str,
        handler: Callable[..., Any],
        on_error: Literal["raise", "log"],
        timeout: float,
    ) -> None:
        self.event = event
        self.handler = handler
        self.on_error = on_error
        self.timeout = timeout


class GraphHookRegistry:
    """Registry for graph-level hook points.

    Passed explicitly to graph builder functions. No global singleton —
    the caller controls what is registered.
    """

    def __init__(self) -> None:
        self._hooks: list[_GraphHookEntry] = []

    def register(
        self,
        event: str,
        handler: Callable[..., Any],
        *,
        on_error: Literal["raise", "log"] = "raise",
        timeout: float = DEFAULT_GRAPH_HOOK_TIMEOUT,
    ) -> None:
        """Register a handler for a graph hook event.

        Args:
            event: Any event string (e.g. ``"before_wave"``).
            handler: Async callable. Receives the observation object,
                returns a modified observation or ``None``.
            on_error: ``"raise"`` (default) propagates errors.
                ``"log"`` swallows errors with logging (use for
                metrics/observability-only hooks).
            timeout: Seconds before the hook is killed.
        """
        self._hooks.append(_GraphHookEntry(event, handler, on_error, timeout))

    async def run(self, event: str, observation: T) -> T:
        """Run all hooks for ``event`` sequentially.

        Each hook receives the observation (possibly modified by prior
        hooks). Returns the final observation.
        """
        matching = [h for h in self._hooks if h.event == event]
        if not matching:
            return observation

        current = observation
        for entry in matching:
            hook_name = entry.handler.__qualname__
            with _tracer.start_as_current_span(
                f"hook.graph.{event}.{hook_name}",
                attributes={
                    "hook.event": event,
                    "hook.handler": hook_name,
                    "hook.on_error": entry.on_error,
                },
            ):
                try:
                    modified = await asyncio.wait_for(
                        entry.handler(current),
                        timeout=entry.timeout,
                    )
                    if modified is not None:
                        current = modified
                except Exception as exc:
                    if entry.on_error == "raise":
                        raise
                    logger.warning(
                        "Graph hook %r for event %r failed (swallowed): %s",
                        hook_name,
                        event,
                        exc,
                    )

        return current

    def has_hooks(self, event: str) -> bool:
        """Check if any hooks are registered for an event."""
        return any(h.event == event for h in self._hooks)

    def registered_hooks(self) -> list[tuple[str, str, str]]:
        """Return all registered hooks as (event, on_error, handler_name) triples."""
        return [(h.event, h.on_error, h.handler.__qualname__) for h in self._hooks]


__all__ = [
    "DEFAULT_GRAPH_HOOK_TIMEOUT",
    "DEFAULT_WORKER_HOOK_TIMEOUT",
    "GraphHookRegistry",
    "HookRegistry",
    "default_hook_registry",
    "merge_context",
    "on_hook",
    "run_after_agent_hooks",
    "run_before_agent_hooks",
]
