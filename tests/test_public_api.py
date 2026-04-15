"""Verify the public API surface is intact and importable."""

from __future__ import annotations


def test_monet_root_exports() -> None:
    import monet

    expected = {
        "agent",
        "AgentManifestHandle",
        "AgentMeta",
        "AgentResult",
        "AgentRunContext",
        "ArtifactPointer",
        "ArtifactStoreHandle",
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
        "get_agent_manifest",
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
        "ArtifactMetadata",
        "ArtifactClient",
        "ArtifactService",
        "FilesystemStorage",
        "InMemoryArtifactClient",
        "SQLiteIndex",
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
    from monet.queue import (  # noqa: F401
        InMemoryTaskQueue,
        TaskQueue,
        TaskRecord,
        TaskStatus,
        run_worker,
    )


def test_server_public_api() -> None:
    from monet.server import (  # noqa: F401
        AgentCapability,
        bootstrap,
        configure_lazy_worker,
    )


def test_client_public_api() -> None:
    """Graph-agnostic client surface — pipeline-specific types live in
    ``monet.pipelines.default``.
    """
    from monet.client import (  # noqa: F401
        AgentProgress,
        AlreadyResolved,
        AmbiguousInterrupt,
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


def test_default_pipeline_public_api() -> None:
    """Default-pipeline surface — typed events and HITL verbs."""
    from monet.pipelines.default import (  # noqa: F401
        DefaultInterruptTag,
        DefaultPipelineEvent,
        DefaultPipelineRunDetail,
        ExecutionInterrupt,
        PlanApproved,
        PlanInterrupt,
        PlanReady,
        ReflectionComplete,
        TriageComplete,
        WaveComplete,
        abort_run,
        approve_plan,
        continue_after_plan_approval,
        reject_plan,
        retry_wave,
        revise_plan,
        run,
    )


def test_artifact_store_handle_public() -> None:
    from monet import ArtifactStoreHandle  # noqa: F401
