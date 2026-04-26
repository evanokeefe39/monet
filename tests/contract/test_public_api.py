"""Verify the public API surface is intact and importable."""

from __future__ import annotations


def test_monet_root_exports() -> None:
    import monet

    expected = {
        "agent",
        "AgentMeta",
        "AgentResult",
        "AgentRunContext",
        "ArtifactPointer",
        "ArtifactStore",
        "GraphHookRegistry",
        "HookRegistry",
        "Signal",
        "SignalType",
        "AUDIT",
        "BLOCKING",
        "INFORMATIONAL",
        "RECOVERABLE",
        "ROUTING",
        "AgentStream",
        "find_artifact",
        "log_handler",
        "on_hook",
        "webhook_handler",
        "write_artifact",
        "get_run_context",
        "get_run_logger",
        "get_artifacts",
        "resolve_context",
        "emit_progress",
        "emit_signal",
        "NeedsHumanReview",
        "EscalationRequired",
        "SemanticError",
    }
    assert set(monet.__all__) == expected
    for name in expected:
        assert getattr(monet, name) is not None, f"{name} is None"


def test_monet_artifacts_exports() -> None:
    import monet.artifacts as cat

    expected = {
        "ArtifactClient",
        "ArtifactReader",
        "ArtifactService",
        "ArtifactStore",
        "ArtifactWriter",
        "InMemoryArtifactClient",
        "artifacts_from_env",
        "configure_artifacts",
    }
    assert set(cat.__all__) == expected
    for name in expected:
        assert getattr(cat, name) is not None, f"{name} is None"


def test_configure_artifacts_callable() -> None:
    from monet.artifacts import configure_artifacts

    assert callable(configure_artifacts)


def test_monet_orchestration_exports() -> None:
    import monet.orchestration as orch

    assert "invoke_agent" in orch.__all__
    assert orch.invoke_agent is not None


def test_agent_result_has_signal_methods() -> None:
    from monet import AgentResult

    assert callable(getattr(AgentResult, "has_signal", None))
    assert callable(getattr(AgentResult, "get_signal", None))


def test_get_artifacts_returns_handle_with_read_and_write() -> None:
    from monet import get_artifacts

    handle = get_artifacts()
    assert callable(getattr(handle, "write", None))
    assert callable(getattr(handle, "read", None))


def test_handle_agent_event_not_in_sdk() -> None:
    """handle_agent_event should not exist in the SDK."""
    import monet.artifacts as cat

    assert not hasattr(cat, "handle_agent_event")


def test_tracing_public_api() -> None:
    from monet.tracing import (  # noqa: F401
        EXECUTION_ROOT_SPAN_NAME,
        RUN_ROOT_SPAN_NAME,
        TRACE_CARRIER_METADATA_KEY,
        attached_trace,
        configure_tracing,
        extract_carrier_from_config,
        get_tracer,
        inject_trace_context,
    )


def test_queue_concrete_exports() -> None:
    from monet.events import TaskRecord, TaskStatus  # noqa: F401
    from monet.queue import InMemoryTaskQueue, TaskQueue  # noqa: F401
    from monet.worker import run_worker  # noqa: F401


def test_server_public_api() -> None:
    from monet.server import create_app  # noqa: F401


def test_client_public_api() -> None:
    """Graph-agnostic client surface. Form-schema + capability types live here."""
    from monet.client import (  # noqa: F401
        AgentProgress,
        AlreadyResolved,
        AmbiguousInterrupt,
        Capability,
        ChatSummary,
        GraphNotInvocable,
        Interrupt,
        InterruptTagMismatch,
        MonetClient,
        MonetClientError,
        NodeUpdate,
        PendingDecision,
        RunComplete,
        RunDetail,
        RunEvent,
        RunFailed,
        RunNotInterrupted,
        RunStarted,
        RunSummary,
        SignalEmitted,
        make_client,
    )


def test_default_graph_public_api() -> None:
    """Compound default graph + form-schema interrupt convention."""
    from monet.client import Field, Form, Interrupt  # noqa: F401
    from monet.orchestration import (  # noqa: F401
        RunState,
        build_default_graph,
        build_execution_subgraph,
        build_planning_subgraph,
    )
