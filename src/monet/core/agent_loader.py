"""Declarative agent registration from ``agents.toml``.

Loads an ``agents.toml`` file, generates thin handler functions for each
declared external agent, and registers them via the ``@agent`` decorator.
All decorator semantics (result wrapping, OTel tracing, hooks, manifest
declaration) are inherited automatically.

Event handlers (``[[agent.on]]``) are resolved eagerly at load time and
registered via ``AgentStream.on_after()`` so they supplement (not replace)
the default SDK handlers.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import tomllib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

logger = logging.getLogger("monet.agents_config")

_VALID_TRANSPORT_TYPES = frozenset({"http", "sse", "cli"})
_VALID_HANDLER_TYPES = frozenset({"python", "bash", "webhook"})
_VALID_EVENT_TYPES = frozenset({"progress", "signal", "artifact", "result", "error"})
# Default timeout for bash/webhook handlers (seconds).
_DEFAULT_HANDLER_TIMEOUT = 5.0


def load_agents(path: Path) -> int:
    """Load ``agents.toml`` and register all declared agents.

    Args:
        path: Path to the ``agents.toml`` file.

    Returns:
        Number of agents registered.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the file contains invalid declarations.
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    entries: list[dict[str, Any]] = raw.get("agent", [])
    if not isinstance(entries, list):
        msg = f"{path}: 'agent' must be an array of tables ([[agent]])"
        raise ValueError(msg)

    for i, entry in enumerate(entries):
        _register_agent(entry, path, i)

    return len(entries)


def _register_agent(entry: dict[str, Any], path: Path, index: int) -> None:
    """Generate a handler function from config and apply ``@agent``."""
    # Validate required fields.
    agent_id = entry.get("id")
    if not agent_id or not isinstance(agent_id, str):
        msg = f"{path}: agent[{index}] missing required 'id' field"
        raise ValueError(msg)

    transport = entry.get("transport")
    if not isinstance(transport, dict):
        msg = f"{path}: agent[{index}] ({agent_id!r}) missing 'transport' table"
        raise ValueError(msg)

    transport_type = transport.get("type")
    if transport_type not in _VALID_TRANSPORT_TYPES:
        msg = (
            f"{path}: agent[{index}] ({agent_id!r}) invalid transport type "
            f"{transport_type!r}. Must be one of: "
            f"{', '.join(sorted(_VALID_TRANSPORT_TYPES))}"
        )
        raise ValueError(msg)

    # Validate transport-specific requirements eagerly.
    if transport_type in ("http", "sse"):
        if not transport.get("url"):
            msg = (
                f"{path}: agent[{index}] ({agent_id!r}) "
                f"{transport_type.upper()} transport requires 'url'"
            )
            raise ValueError(msg)
    elif transport_type == "cli":
        cmd = transport.get("cmd")
        if not cmd or not isinstance(cmd, list):
            msg = (
                f"{path}: agent[{index}] ({agent_id!r}) "
                "CLI transport requires 'cmd' as an array of strings"
            )
            raise ValueError(msg)

    command: str = entry.get("command", "fast")
    pool: str = entry.get("pool", "local")
    description: str = entry.get("description", "")
    allow_empty: bool = entry.get("allow_empty", False)

    # Resolve event handlers eagerly (fail-fast on bad config).
    on_entries: list[dict[str, Any]] = entry.get("on", [])
    resolved_handlers: list[tuple[str, Any]] = []
    for j, on_entry in enumerate(on_entries):
        event_type, handler_fn = _resolve_event_handler(
            on_entry, path, index, agent_id, j
        )
        resolved_handlers.append((event_type, handler_fn))

    # Build the handler via a factory function so loop variables are
    # bound at call time, not captured by late-binding closure.
    handler = _make_handler(dict(transport), agent_id, command, resolved_handlers)

    # Check for duplicate registration before applying.
    from monet.core.registry import default_registry

    if default_registry.exists(agent_id, command):
        msg = (
            f"{path}: agent[{index}] ({agent_id!r}/{command!r}) conflicts with "
            "an already-registered handler (from @agent decorator or another config)"
        )
        raise ValueError(msg)

    # Set docstring before applying @agent (decorator reads first line).
    handler.__doc__ = description or f"External {transport_type} agent"

    # Apply the decorator — gets all wrapping, tracing, hooks, registration.
    from monet.core.decorator import agent

    agent(agent_id=agent_id, command=command, pool=pool, allow_empty=allow_empty)(
        handler
    )

    logger.info(
        "Registered config-declared agent %s/%s (transport=%s, pool=%s)",
        agent_id,
        command,
        transport_type,
        pool,
    )


def _make_handler(
    transport: dict[str, Any],
    agent_id: str,
    command: str,
    event_handlers: list[tuple[str, Any]],
) -> Any:
    """Create an async handler with values bound at creation time.

    The handler signature uses only valid ``AgentRunContext`` field names
    so it passes the ``@agent`` decorator's signature validation.
    """

    async def handler(
        task: str,
        context: list[dict[str, Any]] | None = None,
    ) -> str | None:
        payload = {
            "task": task,
            "context": context or [],
            "command": command,
            "agent_id": agent_id,
        }
        stream = _build_stream(transport, payload)
        # Register config-declared event handlers (supplement defaults).
        for event_type, handler_fn in event_handlers:
            stream.on_after(event_type, handler_fn)
        result: str | None = await stream.run()
        return result

    return handler


def _build_stream(transport: dict[str, Any], payload: dict[str, Any]) -> Any:
    """Dispatch to the appropriate ``AgentStream`` constructor.

    Extension point: add a new ``transport_type`` branch here to support
    additional transports. There is no registry — each transport is a named
    branch in this function.
    """
    from monet.streams import AgentStream

    transport_type = transport["type"]
    timeout = float(transport.get("timeout", 30.0))

    if transport_type == "http":
        url = transport.get("url")
        if not url:
            msg = "HTTP transport requires 'url'"
            raise ValueError(msg)
        return AgentStream.http_post(url, payload, timeout=timeout)

    if transport_type == "sse":
        url = transport.get("url")
        if not url:
            msg = "SSE transport requires 'url'"
            raise ValueError(msg)
        return AgentStream.sse_post(url, payload, timeout=timeout)

    if transport_type == "cli":
        cmd = transport.get("cmd")
        if not cmd or not isinstance(cmd, list):
            msg = "CLI transport requires 'cmd' as an array of strings"
            raise ValueError(msg)
        return AgentStream.cli(cmd, stdin_payload=payload)

    # Unreachable given _VALID_TRANSPORT_TYPES check, but guard anyway.
    msg = f"Unknown transport type: {transport_type!r}"
    raise ValueError(msg)


# ── Event handler resolution ─────────────────────────────────────────────


def _resolve_event_handler(
    entry: dict[str, Any],
    path: Path,
    agent_index: int,
    agent_id: str,
    handler_index: int,
) -> tuple[str, Callable[..., Any] | Callable[..., Awaitable[Any]]]:
    """Validate and resolve a single ``[[agent.on]]`` entry.

    Returns ``(event_type, handler_callable)``.

    Raises:
        ValueError: On missing/invalid fields.
    """
    prefix = f"{path}: agent[{agent_index}] ({agent_id!r}) on[{handler_index}]"

    event = entry.get("event")
    if event not in _VALID_EVENT_TYPES:
        msg = (
            f"{prefix}: invalid event {event!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_EVENT_TYPES))}"
        )
        raise ValueError(msg)

    handler_type = entry.get("type")
    if handler_type not in _VALID_HANDLER_TYPES:
        msg = (
            f"{prefix}: invalid handler type {handler_type!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_HANDLER_TYPES))}"
        )
        raise ValueError(msg)

    if handler_type == "python":
        return event, _import_python_handler(entry, prefix)
    if handler_type == "bash":
        return event, _make_bash_handler(entry, prefix)
    # webhook
    return event, _make_webhook_handler(entry, prefix)


def _import_python_handler(
    entry: dict[str, Any],
    prefix: str,
) -> Callable[..., Any]:
    """Import a Python handler from a ``module:function`` spec."""
    spec = entry.get("handler")
    if not spec or not isinstance(spec, str) or ":" not in spec:
        msg = (
            f"{prefix}: python handler requires "
            "'handler' as 'module.path:function_name'"
        )
        raise ValueError(msg)

    module_path, func_name = spec.rsplit(":", 1)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        msg = f"{prefix}: could not import module {module_path!r}: {exc}"
        raise ValueError(msg) from exc

    func = getattr(module, func_name, None)
    if not callable(func):
        msg = f"{prefix}: {func_name!r} not found or not callable in {module_path!r}"
        raise ValueError(msg)

    return func  # type: ignore[no-any-return]


def _make_bash_handler(
    entry: dict[str, Any],
    prefix: str,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Create an async handler that runs a bash command with event JSON on stdin."""
    cmd = entry.get("cmd")
    if not cmd or not isinstance(cmd, str):
        msg = f"{prefix}: bash handler requires 'cmd' as a string"
        raise ValueError(msg)

    timeout = float(entry.get("timeout", _DEFAULT_HANDLER_TIMEOUT))

    async def handler(event: dict[str, Any]) -> None:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(event).encode()),
                timeout=timeout,
            )
        except TimeoutError:
            if proc.returncode is None:
                proc.kill()
            logger.warning(
                "bash handler %r timed out after %.1fs",
                cmd,
                timeout,
            )
            return
        if proc.returncode != 0:
            logger.warning(
                "bash handler %r exited %d: %s",
                cmd,
                proc.returncode,
                stderr.decode(errors="replace")[:200],
            )

    return handler


def _make_webhook_handler(
    entry: dict[str, Any],
    prefix: str,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Create an async webhook handler using the existing factory."""
    url = entry.get("url")
    if not url or not isinstance(url, str):
        msg = f"{prefix}: webhook handler requires 'url'"
        raise ValueError(msg)

    timeout = float(entry.get("timeout", _DEFAULT_HANDLER_TIMEOUT))

    from monet.handlers import webhook_handler

    return webhook_handler(url, timeout=timeout)
