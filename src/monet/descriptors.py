"""Agent capability descriptors — static typed configuration.

Loaded at startup. Not a runtime service. Each agent has a descriptor
defining commands, calling conventions, SLA characteristics, and retry
semantics.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from ._types import Effort  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Generator


class SLACharacteristics(BaseModel):
    """Expected performance characteristics per effort level."""

    expected_latency_ms: dict[str, int] = {}
    cost_tier: str = "standard"


class RetryConfig(BaseModel):
    """Retry semantics for a command."""

    max_retries: int = 3
    retryable_errors: list[str] = ["unexpected_error"]
    backoff_factor: float = 1.0


class CommandDescriptor(BaseModel):
    """Descriptor for a single agent command."""

    calling_convention: Literal["sync", "async"] = "sync"
    effort_vocabulary: list[Effort] = ["low", "medium", "high"]
    sla: SLACharacteristics = SLACharacteristics()
    retry: RetryConfig = RetryConfig()


class AgentDescriptor(BaseModel):
    """Capability descriptor for an agent."""

    agent_id: str
    description: str = ""
    commands: dict[str, CommandDescriptor] = {}
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
        """Load and register a descriptor from a dict."""
        descriptor = AgentDescriptor(**data)
        self.register(descriptor)
        return descriptor


default_descriptor_registry = DescriptorRegistry()
