"""Tests for capability descriptors."""

from __future__ import annotations

from monet.descriptors import (
    AgentDescriptor,
    CommandDescriptor,
    DescriptorRegistry,
    RetryConfig,
    SLACharacteristics,
)


def test_command_descriptor_defaults() -> None:
    cmd = CommandDescriptor()
    assert cmd.calling_convention == "sync"
    assert cmd.effort_vocabulary == ["low", "medium", "high"]
    assert cmd.retry.max_retries == 3


def test_command_descriptor_async() -> None:
    cmd = CommandDescriptor(calling_convention="async")
    assert cmd.calling_convention == "async"


def test_sla_characteristics() -> None:
    sla = SLACharacteristics(
        expected_latency_ms={"low": 500, "medium": 2000, "high": 10000},
        cost_tier="premium",
    )
    assert sla.expected_latency_ms["high"] == 10000
    assert sla.cost_tier == "premium"


def test_retry_config() -> None:
    retry = RetryConfig(
        max_retries=5,
        retryable_errors=["unexpected_error", "timeout"],
        backoff_factor=2.0,
    )
    assert retry.max_retries == 5
    assert "timeout" in retry.retryable_errors


def test_agent_descriptor() -> None:
    desc = AgentDescriptor(
        agent_id="researcher",
        description="Information gathering",
        commands={
            "fast": CommandDescriptor(calling_convention="sync"),
            "deep": CommandDescriptor(
                calling_convention="async",
                sla=SLACharacteristics(expected_latency_ms={"high": 30000}),
            ),
        },
    )
    assert desc.agent_id == "researcher"
    assert desc.commands["deep"].calling_convention == "async"
    assert desc.commands["deep"].sla.expected_latency_ms["high"] == 30000


def test_load_from_dict() -> None:
    reg = DescriptorRegistry()
    desc = reg.load_from_dict(
        {
            "agent_id": "writer",
            "description": "Content production",
            "commands": {
                "fast": {"calling_convention": "sync"},
                "deep": {"calling_convention": "async"},
            },
        }
    )
    assert desc.agent_id == "writer"
    assert reg.lookup("writer") is desc


def test_registry_lookup_missing() -> None:
    reg = DescriptorRegistry()
    assert reg.lookup("nonexistent") is None


def test_registry_scope() -> None:
    reg = DescriptorRegistry()
    desc = AgentDescriptor(agent_id="temp")
    reg.register(desc)

    with reg.registry_scope():
        reg.register(AgentDescriptor(agent_id="scoped"))
        assert reg.lookup("scoped") is not None

    assert reg.lookup("scoped") is None
    assert reg.lookup("temp") is not None
