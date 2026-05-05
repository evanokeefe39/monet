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
import uuid
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

if TYPE_CHECKING:
    from pathlib import Path

import logging

logger = logging.getLogger("monet.agents_config")


# ── Typed config models (extra="forbid" so typos surface immediately) ────────


class AgentHTTPRequest(BaseModel):
    """JSONPath request body template for protocol='http'."""

    model_config = ConfigDict(extra="forbid")

    body: dict[str, Any] = {}
    params: dict[str, str] = {}
    method: str = "POST"


class AgentHTTPResponse(BaseModel):
    """JSONPath response extraction for protocol='http'."""

    model_config = ConfigDict(extra="forbid")

    output: str = "$.output"


class AgentTransportConfig(BaseModel):
    """Transport declaration for a config-declared agent."""

    model_config = ConfigDict(extra="forbid")

    protocol: Literal["openai", "http", "zeroclaw", "custom"]
    url: str | None = None
    timeout: float = 30.0
    model: str | None = None
    request: AgentHTTPRequest | None = None
    response: AgentHTTPResponse | None = None
    adapter: str | None = None
    config_dir: str | None = None

    @model_validator(mode="after")
    def _check_requirements(self) -> AgentTransportConfig:
        if self.protocol in ("openai", "http") and not self.url:
            raise ValueError(f"{self.protocol!r} protocol requires 'url'")
        if self.protocol == "custom" and not self.adapter:
            raise ValueError("'custom' protocol requires 'adapter'")
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

    handler.__doc__ = (
        config.description or f"External {config.transport.protocol} agent"
    )

    from monet.core.decorator import agent

    agent(
        agent_id=config.id,
        command=config.command,
        pool=config.pool,
        allow_empty=config.allow_empty,
    )(handler)

    logger.info(
        "Registered config-declared agent %s/%s (protocol=%s, pool=%s)",
        config.id,
        config.command,
        config.transport.protocol,
        config.pool,
    )


def _make_handler(
    transport: AgentTransportConfig,
    agent_id: str,
    command: str,
) -> Any:
    """Create an async handler backed by a native protocol caller."""
    caller = _make_protocol_caller(transport)

    async def handler(
        task: str,
        context: list[dict[str, Any]] | None = None,
    ) -> str | None:
        ctx: dict[str, Any] = {
            "task": task,
            "task_id": str(uuid.uuid4()),
            "context": context or [],
            "command": command,
            "agent_id": agent_id,
        }
        result = await caller(task, ctx)
        return result if isinstance(result, str) else None

    return handler


def _make_protocol_caller(transport: AgentTransportConfig) -> Any:
    from monet.worker.transport._direct import (
        custom_caller,
        http_caller,
        openai_caller,
        zeroclaw_caller,
    )

    if transport.protocol == "openai":
        assert transport.url is not None
        return openai_caller(transport.url, transport.model, transport.timeout)

    if transport.protocol == "http":
        assert transport.url is not None
        return http_caller(
            transport.url,
            transport.request or AgentHTTPRequest(),
            transport.response or AgentHTTPResponse(),
            transport.timeout,
        )

    if transport.protocol == "zeroclaw":
        return zeroclaw_caller(transport.config_dir, transport.timeout)

    if transport.protocol == "custom":
        assert transport.adapter is not None
        return custom_caller(transport.adapter, transport.url, transport.timeout)

    raise ValueError(f"Unknown protocol: {transport.protocol!r}")  # pragma: no cover
