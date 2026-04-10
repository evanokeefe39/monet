"""Tests for the agent registry."""

from __future__ import annotations

from monet.core.registry import AgentRegistry, default_registry


def _dummy_handler() -> str:
    return "dummy"


def test_register_and_lookup() -> None:
    reg = AgentRegistry()
    reg.register("agent-a", "fast", _dummy_handler)
    assert reg.lookup("agent-a", "fast") is _dummy_handler


def test_lookup_missing() -> None:
    reg = AgentRegistry()
    assert reg.lookup("nonexistent", "fast") is None


def test_clear() -> None:
    reg = AgentRegistry()
    reg.register("agent-a", "fast", _dummy_handler)
    reg.clear()
    assert reg.lookup("agent-a", "fast") is None
    assert reg.registered_agents() == []


def test_registry_scope_restores() -> None:
    reg = AgentRegistry()
    reg.register("agent-a", "fast", _dummy_handler)

    with reg.registry_scope():
        reg.register("agent-b", "deep", _dummy_handler)
        assert reg.lookup("agent-b", "deep") is _dummy_handler

    # After scope, agent-b should be gone, agent-a restored
    assert reg.lookup("agent-b", "deep") is None
    assert reg.lookup("agent-a", "fast") is _dummy_handler


def test_registry_scope_on_exception() -> None:
    reg = AgentRegistry()
    reg.register("agent-a", "fast", _dummy_handler)

    try:
        with reg.registry_scope():
            reg.register("agent-x", "fast", _dummy_handler)
            raise ValueError("boom")
    except ValueError:
        pass

    # Registry should be restored despite exception
    assert reg.lookup("agent-x", "fast") is None
    assert reg.lookup("agent-a", "fast") is _dummy_handler


def test_same_agent_different_commands() -> None:
    reg = AgentRegistry()
    fast_handler = lambda: "fast"  # noqa: E731
    deep_handler = lambda: "deep"  # noqa: E731
    reg.register("writer", "fast", fast_handler)
    reg.register("writer", "deep", deep_handler)
    assert reg.lookup("writer", "fast") is fast_handler
    assert reg.lookup("writer", "deep") is deep_handler


def test_registered_agents() -> None:
    reg = AgentRegistry()
    reg.register("a", "fast", _dummy_handler)
    reg.register("b", "deep", _dummy_handler)
    agents = reg.registered_agents()
    assert ("a", "fast") in agents
    assert ("b", "deep") in agents


def test_default_registry_exists() -> None:
    assert isinstance(default_registry, AgentRegistry)
