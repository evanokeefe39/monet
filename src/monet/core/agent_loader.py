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
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

logger = logging.getLogger("monet.agents_config")


# ── Typed config models (extra="forbid" so typos surface immediately) ────────


class AgentTransportConfig(BaseModel):
    """Transport declaration for a config-declared agent."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["http", "sse", "cli"]
    url: str | None = None
    cmd: list[str] | None = None
    timeout: float = 30.0

    @model_validator(mode="after")
    def _check_requirements(self) -> AgentTransportConfig:
        if self.type in ("http", "sse") and not self.url:
            raise ValueError(f"{self.type.upper()} transport requires 'url'")
        if self.type == "cli" and not self.cmd:
            raise ValueError("CLI transport requires 'cmd' as an array of strings")
        return self


class AgentEventHandlerConfig(BaseModel):
    """A single ``[[agent.on]]`` event handler declaration."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["progress", "signal", "artifact", "result", "error"]
    type: Literal["python", "bash", "webhook"]
    handler: str | None = None  # python handler: "module.path:function_name"
    cmd: str | None = None  # bash handler: shell command string
    url: str | None = None  # webhook handler: target URL
    timeout: float = 5.0


class AgentEntryConfig(BaseModel):
    """Full declaration for a single ``[[agent]]`` entry."""

    model_config = ConfigDict(extra="forbid")

    id: str
    transport: AgentTransportConfig
    command: str = "fast"
    pool: str = "local"
    description: str = ""
    allow_empty: bool = False
    on: list[AgentEventHandlerConfig] = []


# ── Public entry point ───────────────────────────────────────────────────────


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
        try:
            config = AgentEntryConfig.model_validate(entry)
        except ValidationError as exc:
            raise ValueError(f"{path}: agent[{i}] invalid declaration: {exc}") from exc
        _register_agent(config, path, i)

    return len(entries)


# ── Private helpers ──────────────────────────────────────────────────────────


def _register_agent(config: AgentEntryConfig, path: Path, index: int) -> None:
    """Generate a handler function from config and apply ``@agent``."""
    resolved_handlers: list[tuple[str, Any]] = []
    for j, on_cfg in enumerate(config.on):
        event_type, handler_fn = _resolve_event_handler(
            on_cfg, path, index, config.id, j
        )
        resolved_handlers.append((event_type, handler_fn))

    handler = _make_handler(
        config.transport, config.id, config.command, resolved_handlers
    )

    from monet.core.registry import default_registry

    if default_registry.exists(config.id, config.command):
        msg = (
            f"{path}: agent[{index}] ({config.id!r}/{config.command!r}) conflicts with "
            "an already-registered handler (from @agent decorator or another config)"
        )
        raise ValueError(msg)

    handler.__doc__ = config.description or f"External {config.transport.type} agent"

    from monet.core.decorator import agent

    agent(
        agent_id=config.id,
        command=config.command,
        pool=config.pool,
        allow_empty=config.allow_empty,
    )(handler)

    logger.info(
        "Registered config-declared agent %s/%s (transport=%s, pool=%s)",
        config.id,
        config.command,
        config.transport.type,
        config.pool,
    )


def _make_handler(
    transport: AgentTransportConfig,
    agent_id: str,
    command: str,
    event_handlers: list[tuple[str, Any]],
) -> Any:
    """Create an async handler with values bound at creation time."""

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
        for event_type, handler_fn in event_handlers:
            stream.on_after(event_type, handler_fn)
        result: str | None = await stream.run()
        return result

    return handler


def _build_stream(transport: AgentTransportConfig, payload: dict[str, Any]) -> Any:
    """Dispatch to the appropriate ``AgentStream`` constructor."""
    from monet.streams import AgentStream

    if transport.type == "http":
        return AgentStream.http_post(transport.url, payload, timeout=transport.timeout)  # type: ignore[arg-type]

    if transport.type == "sse":
        return AgentStream.sse_post(transport.url, payload, timeout=transport.timeout)  # type: ignore[arg-type]

    # cli — transport.cmd is guaranteed non-None by AgentTransportConfig validator
    return AgentStream.cli(transport.cmd, stdin_payload=payload)  # type: ignore[arg-type]


# ── Event handler resolution ─────────────────────────────────────────────────


def _resolve_event_handler(
    cfg: AgentEventHandlerConfig,
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

    if cfg.type == "python":
        return cfg.event, _import_python_handler(cfg, prefix)
    if cfg.type == "bash":
        return cfg.event, _make_bash_handler(cfg, prefix)
    return cfg.event, _make_webhook_handler(cfg, prefix)


def _import_python_handler(
    cfg: AgentEventHandlerConfig,
    prefix: str,
) -> Callable[..., Any]:
    """Import a Python handler from a ``module:function`` spec."""
    spec = cfg.handler
    if not spec or ":" not in spec:
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
    cfg: AgentEventHandlerConfig,
    prefix: str,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Create an async handler that runs a bash command with event JSON on stdin."""
    if not cfg.cmd:
        msg = f"{prefix}: bash handler requires 'cmd' as a string"
        raise ValueError(msg)

    cmd = cfg.cmd
    timeout = cfg.timeout

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
    cfg: AgentEventHandlerConfig,
    prefix: str,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Create an async webhook handler using the existing factory."""
    if not cfg.url:
        msg = f"{prefix}: webhook handler requires 'url'"
        raise ValueError(msg)

    from monet.handlers import webhook_handler

    return webhook_handler(cfg.url, timeout=cfg.timeout)
