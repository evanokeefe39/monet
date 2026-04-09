"""Shared test fixtures for the monet test suite."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from monet._registry import default_registry
from monet.catalogue import InMemoryCatalogueClient, configure_catalogue

if TYPE_CHECKING:
    from monet.types import AgentRunContext


def make_ctx(**overrides: Any) -> AgentRunContext:
    """Build an AgentRunContext dict with defaults."""
    base: AgentRunContext = {
        "task": "",
        "context": [],
        "command": "fast",
        "trace_id": "",
        "run_id": "",
        "agent_id": "",
        "skills": [],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


@pytest.fixture
def clean_registry() -> Any:
    """Isolate each test from registry side effects."""
    with default_registry.registry_scope():
        yield


@pytest.fixture
def catalogue() -> Any:
    """Provide an in-memory catalogue backend."""
    configure_catalogue(InMemoryCatalogueClient())
    yield
    configure_catalogue(None)
