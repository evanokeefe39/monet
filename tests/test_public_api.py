"""Verify the public API surface is intact and importable."""

from __future__ import annotations


def test_monet_root_exports() -> None:
    import monet

    expected = {
        "agent",
        "AgentResult",
        "AgentRunContext",
        "ArtifactPointer",
        "Signal",
        "SignalType",
        "AUDIT",
        "BLOCKING",
        "INFORMATIONAL",
        "RECOVERABLE",
        "ROUTING",
        "AgentStream",
        "log_handler",
        "webhook_handler",
        "write_artifact",
        "get_run_context",
        "get_run_logger",
        "get_catalogue",
        "emit_progress",
        "emit_signal",
        "NeedsHumanReview",
        "EscalationRequired",
        "SemanticError",
    }
    assert set(monet.__all__) == expected
    for name in expected:
        assert getattr(monet, name) is not None, f"{name} is None"


def test_monet_catalogue_exports() -> None:
    import monet.catalogue as cat

    expected = {
        "ArtifactMetadata",
        "CatalogueClient",
        "CatalogueService",
        "FilesystemStorage",
        "InMemoryCatalogueClient",
        "SQLiteIndex",
        "configure_catalogue",
    }
    assert set(cat.__all__) == expected
    for name in expected:
        assert getattr(cat, name) is not None, f"{name} is None"


def test_configure_catalogue_callable() -> None:
    from monet.catalogue import configure_catalogue

    assert callable(configure_catalogue)


def test_monet_orchestration_exports() -> None:
    import monet.orchestration as orch

    assert "invoke_agent" in orch.__all__
    assert orch.invoke_agent is not None


def test_agent_result_has_signal_methods() -> None:
    from monet import AgentResult

    assert callable(getattr(AgentResult, "has_signal", None))
    assert callable(getattr(AgentResult, "get_signal", None))


def test_get_catalogue_returns_handle_with_read_and_write() -> None:
    from monet import get_catalogue

    handle = get_catalogue()
    assert callable(getattr(handle, "write", None))
    assert callable(getattr(handle, "read", None))


def test_handle_agent_event_not_in_sdk() -> None:
    """handle_agent_event should not exist in the SDK — applications inline their own dispatch."""
    import monet.catalogue as cat

    assert not hasattr(cat, "handle_agent_event")
