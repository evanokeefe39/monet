"""Agent capability descriptors — static typed configuration.

Loaded at startup. Not a runtime service. Each agent has a descriptor
defining commands, calling conventions, SLA characteristics, and retry
semantics.
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Generator

Effort = Literal["low", "medium", "high"]


@dataclass
class SLACharacteristics:
    """Expected performance characteristics per effort level."""

    expected_latency_ms: dict[str, int] = field(default_factory=dict)
    cost_tier: str = "standard"


@dataclass
class RetryConfig:
    """Retry semantics for a command."""

    max_retries: int = 3
    retryable_errors: list[str] = field(default_factory=lambda: ["unexpected_error"])
    backoff_factor: float = 1.0


@dataclass
class CommandDescriptor:
    """Descriptor for a single agent command."""

    calling_convention: Literal["sync", "async"] = "sync"
    effort_vocabulary: list[Effort] = field(
        default_factory=lambda: ["low", "medium", "high"]
    )
    sla: SLACharacteristics = field(default_factory=SLACharacteristics)
    retry: RetryConfig = field(default_factory=RetryConfig)


@dataclass
class AgentDescriptor:
    """Capability descriptor for an agent."""

    agent_id: str = ""
    description: str = ""
    commands: dict[str, CommandDescriptor] = field(default_factory=dict)
    confidence_model: str = "self-reported"


class DescriptorRegistry:
    """Registry for agent descriptors. Same pattern as AgentRegistry."""

    def __init__(self) -> None:
        self._descriptors: dict[str, AgentDescriptor] = {}
        self._lock = threading.Lock()

    def register(self, descriptor: AgentDescriptor) -> None:
        """Register a descriptor for an agent."""
        with self._lock:
            self._descriptors[descriptor.agent_id] = descriptor

    def lookup(self, agent_id: str) -> AgentDescriptor | None:
        """Look up a descriptor. Returns None if not found."""
        return self._descriptors.get(agent_id)

    def clear(self) -> None:
        """Remove all descriptors."""
        with self._lock:
            self._descriptors.clear()

    @contextlib.contextmanager
    def registry_scope(self) -> Generator[None]:
        """Snapshot and restore descriptor state for test isolation."""
        with self._lock:
            snapshot = dict(self._descriptors)
        try:
            yield
        finally:
            with self._lock:
                self._descriptors = snapshot

    def load_from_dict(self, data: dict[str, Any]) -> AgentDescriptor:
        """Load and register a descriptor from a dict.

        Nested dicts are converted to their respective dataclass types.
        """
        commands_data = data.get("commands", {})
        commands: dict[str, CommandDescriptor] = {}
        for cmd_name, cmd_dict in commands_data.items():
            if isinstance(cmd_dict, dict):
                sla_data = cmd_dict.pop("sla", None)
                retry_data = cmd_dict.pop("retry", None)
                cmd = CommandDescriptor(**cmd_dict)
                if sla_data:
                    cmd.sla = SLACharacteristics(**sla_data)
                if retry_data:
                    cmd.retry = RetryConfig(**retry_data)
                commands[cmd_name] = cmd
            else:
                commands[cmd_name] = cmd_dict

        descriptor = AgentDescriptor(
            agent_id=data.get("agent_id", ""),
            description=data.get("description", ""),
            commands=commands,
            confidence_model=data.get("confidence_model", "self-reported"),
        )
        self.register(descriptor)
        return descriptor


default_descriptor_registry = DescriptorRegistry()
