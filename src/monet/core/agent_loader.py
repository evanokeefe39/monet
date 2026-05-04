"""Declarative agent registration from ``agents.toml``.

Loads an ``agents.toml`` file, generates thin handler functions for each
declared external agent, and registers them via the ``@agent`` decorator.
All decorator semantics (result wrapping, OTel tracing, hooks, manifest
declaration) are inherited automatically.

The ``[[agent.on]]`` event-handler subsystem has been removed. Agents
communicate with the platform through the data plane gateway.
"""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

if TYPE_CHECKING:
    from pathlib import Path

import logging

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


class AgentEntryConfig(BaseModel):
    """Full declaration for a single ``[[agent]]`` entry."""

    model_config = ConfigDict(extra="forbid")

    id: str
    transport: AgentTransportConfig
    command: str = "fast"
    pool: str = "local"
    description: str = ""
    allow_empty: bool = False

    @model_validator(mode="before")
    @classmethod
    def _reject_on_handlers(cls, data: Any) -> Any:
        if isinstance(data, dict) and "on" in data:
            raise ValueError(
                "[[agent.on]] event handlers are no longer supported. "
                "Agents communicate with the platform through the data plane gateway. "
                "See docs/architecture/worker-composition-plan.md "
                "for the migration guide."
            )
        return data


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
    handler = _make_handler(config.transport, config.id, config.command)

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
) -> Any:
    """Create an async handler backed by a transport adapter."""

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
        adapter = _resolve_adapter(transport)
        endpoint = _make_endpoint(transport)
        session = await adapter.connect(endpoint)
        try:
            await session.submit(payload)
            async for event in session.receive():
                if event.type == "result":
                    output = event.data.get("output")
                    return output if isinstance(output, str) else None
        finally:
            await session.close()
        return None

    return handler


def _resolve_adapter(transport: AgentTransportConfig) -> Any:
    """Return the transport adapter matching *transport.type*."""
    if transport.type == "http":
        from monet.worker.transport._http import HTTPTransport

        return HTTPTransport()
    if transport.type == "sse":
        from monet.worker.transport._sse import SSETransport

        return SSETransport()
    # cli — transport.cmd is guaranteed non-None by AgentTransportConfig validator
    from monet.worker.transport._cli import CLITransport

    return CLITransport()


def _make_endpoint(transport: AgentTransportConfig) -> Any:
    """Build an Endpoint for direct (non-worker) transport invocation."""
    from monet.worker.execution._protocol import Endpoint

    metadata: dict[str, Any] = {}
    if transport.type == "cli" and transport.cmd:
        metadata["cmd"] = transport.cmd

    return Endpoint(
        address=transport.url or "",
        process_id="direct",
        backend_type="none",
        metadata=metadata,
    )
