"""Tests for AgentManifestHandle and configure_agent_manifest()."""

from __future__ import annotations

import pytest

from monet import get_agent_manifest
from monet.agent_manifest import configure_agent_manifest
from monet.core.manifest import AgentManifest, default_manifest


@pytest.fixture
def _reset_manifest_handle() -> None:  # type: ignore[misc]
    """Capture and restore the current backend."""
    from monet.core import agent_manifest as _mod

    original = _mod._backend
    yield
    configure_agent_manifest(original)


def test_no_backend_list_agents_raises(_reset_manifest_handle: None) -> None:
    configure_agent_manifest(None)
    with pytest.raises(RuntimeError, match="requires a backend"):
        get_agent_manifest().list_agents()


def test_no_backend_is_available_returns_false(_reset_manifest_handle: None) -> None:
    configure_agent_manifest(None)
    assert get_agent_manifest().is_available("x", "y") is False


def test_no_backend_get_pool_returns_none(_reset_manifest_handle: None) -> None:
    configure_agent_manifest(None)
    assert get_agent_manifest().get_pool("x", "y") is None


def test_is_configured_false_then_true(_reset_manifest_handle: None) -> None:
    configure_agent_manifest(None)
    assert get_agent_manifest().is_configured() is False
    configure_agent_manifest(default_manifest)
    assert get_agent_manifest().is_configured() is True


def test_list_agents_returns_capabilities(_reset_manifest_handle: None) -> None:
    scoped = AgentManifest()
    scoped.declare("test-agent", "run", description="Test", pool="local")
    configure_agent_manifest(scoped)
    caps = get_agent_manifest().list_agents()
    assert len(caps) == 1
    assert caps[0]["agent_id"] == "test-agent"
    assert caps[0]["command"] == "run"


def test_get_pool_for_declared_agent(_reset_manifest_handle: None) -> None:
    scoped = AgentManifest()
    scoped.declare("ag", "cmd", pool="remote-pool")
    configure_agent_manifest(scoped)
    assert get_agent_manifest().get_pool("ag", "cmd") == "remote-pool"


def test_is_available_for_declared_agent(_reset_manifest_handle: None) -> None:
    scoped = AgentManifest()
    scoped.declare("ag", "cmd")
    configure_agent_manifest(scoped)
    assert get_agent_manifest().is_available("ag", "cmd") is True
    assert get_agent_manifest().is_available("ag", "other") is False


def test_configure_none_resets_backend(_reset_manifest_handle: None) -> None:
    configure_agent_manifest(default_manifest)
    assert get_agent_manifest().is_configured() is True
    configure_agent_manifest(None)
    assert get_agent_manifest().is_configured() is False
