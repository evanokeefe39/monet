"""Tests for OTel tracing utilities."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

import pytest

from monet.core.tracing import (
    _apply_honeycomb_shortcut,
    _apply_langfuse_shortcut,
    _apply_langsmith_shortcut,
    configure_tracing,
    get_tracer,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def clean_otel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "HONEYCOMB_API_KEY",
        "HONEYCOMB_DATASET",
    ):
        monkeypatch.delenv(var, raising=False)


def test_get_tracer() -> None:
    tracer = get_tracer()
    assert tracer is not None


def test_get_tracer_with_name() -> None:
    tracer = get_tracer("my.module")
    assert tracer is not None


def test_configure_tracing_idempotent() -> None:
    """configure_tracing() can be called multiple times safely."""
    configure_tracing()
    configure_tracing()
    configure_tracing(service_name="custom")


def test_langfuse_shortcut_derives_endpoint_and_header(
    clean_otel_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    _apply_langfuse_shortcut()
    import os

    assert (
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]
        == "http://localhost:3000/api/public/otel"
    )
    expected = base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    assert os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == f"Authorization=Basic {expected}"


def test_langsmith_shortcut(
    clean_otel_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
    monkeypatch.setenv("LANGSMITH_PROJECT", "monet")
    _apply_langsmith_shortcut()
    import os

    assert (
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]
        == "https://api.smith.langchain.com/otel/v1/traces"
    )
    assert os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == (
        "x-api-key=lsv2_test,Langsmith-Project=monet"
    )


def test_honeycomb_shortcut(
    clean_otel_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HONEYCOMB_API_KEY", "hc_test")
    monkeypatch.setenv("HONEYCOMB_DATASET", "monet")
    _apply_honeycomb_shortcut()
    import os

    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://api.honeycomb.io"
    assert os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == (
        "x-honeycomb-team=hc_test,x-honeycomb-dataset=monet"
    )


def test_shortcut_does_not_override_explicit_otel_vars(
    clean_otel_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://explicit.example/otel")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-custom=value")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
    monkeypatch.setenv("HONEYCOMB_API_KEY", "hc_test")
    _apply_langsmith_shortcut()
    _apply_honeycomb_shortcut()
    import os

    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://explicit.example/otel"
    assert os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == "x-custom=value"


def test_trace_context_propagates_across_extract_attach() -> None:
    """Spans opened after re-attaching an injected carrier share the
    same trace_id as the parent span. This is the mechanism that makes
    every agent span in one monet run part of a single Langfuse trace."""
    from monet.core.tracing import (
        detach_trace_context,
        extract_and_attach_trace_context,
        inject_trace_context,
    )

    parent_tracer = get_tracer("monet.test.parent")
    child_tracer = get_tracer("monet.test.child")

    with parent_tracer.start_as_current_span("root") as parent:
        parent_trace_id = parent.get_span_context().trace_id
        carrier = inject_trace_context()
    # Parent span is now ended. Simulate a downstream node re-attaching
    # the carrier before opening its own span.
    assert carrier  # carrier is non-empty (contains traceparent)
    token = extract_and_attach_trace_context(carrier)
    try:
        with child_tracer.start_as_current_span("child") as child:
            child_trace_id = child.get_span_context().trace_id
    finally:
        detach_trace_context(token)
    assert child_trace_id == parent_trace_id


def test_tracer_creates_span() -> None:
    """Tracer can create spans via context manager."""
    tracer = get_tracer("monet.agent")
    with tracer.start_as_current_span(
        "agent.test.fast",
        attributes={"agent.id": "test", "agent.command": "fast"},
    ) as span:
        span.set_attribute("agent.success", True)
    # No assertion needed — just verify no exceptions


def test_file_exporter_writes_jsonl(
    clean_otel_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spans opened while MONET_TRACE_FILE is set land as JSONL on disk."""
    from monet.core import tracing

    path = tmp_path / "traces.jsonl"
    monkeypatch.setenv("MONET_TRACE_FILE", str(path))
    # The module-level flag persists across tests — reset it so this test
    # actually attaches a fresh processor against its own tmp_path.
    monkeypatch.setattr(tracing, "_file_exporter_attached", False)

    tracing.configure_tracing()

    tracer = tracing.get_tracer("test.file_exporter")
    with tracer.start_as_current_span("test.span") as span:
        span.set_attribute("monet.test", "value")

    assert tracing._provider is not None
    tracing._provider.force_flush()

    content = path.read_text(encoding="utf-8").strip()
    assert content, "trace file should not be empty"
    records = [json.loads(line) for line in content.splitlines()]
    assert any(rec.get("name") == "test.span" for rec in records)


def test_file_exporter_idempotent(
    clean_otel_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated configure_tracing() calls do not duplicate the exporter."""
    from monet.core import tracing

    path = tmp_path / "idempotent.jsonl"
    monkeypatch.setenv("MONET_TRACE_FILE", str(path))
    monkeypatch.setattr(tracing, "_file_exporter_attached", False)

    tracing.configure_tracing()
    tracing.configure_tracing()
    tracing.configure_tracing()

    tracer = tracing.get_tracer("test.idempotent")
    with tracer.start_as_current_span("once"):
        pass

    assert tracing._provider is not None
    tracing._provider.force_flush()

    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").strip().splitlines()
    ]
    # Span name "once" should appear exactly once — no duplicates from
    # multiple stacked processors.
    assert sum(1 for rec in records if rec.get("name") == "once") == 1


def test_file_exporter_creates_parent_dir(
    clean_otel_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nonexistent parent directories are created lazily on exporter init."""
    from monet.core import tracing

    path = tmp_path / "nested" / "deeper" / "traces.jsonl"
    assert not path.parent.exists()
    monkeypatch.setenv("MONET_TRACE_FILE", str(path))
    monkeypatch.setattr(tracing, "_file_exporter_attached", False)

    tracing.configure_tracing()

    tracer = tracing.get_tracer("test.nested")
    with tracer.start_as_current_span("nested.span"):
        pass

    assert tracing._provider is not None
    tracing._provider.force_flush()

    assert path.parent.is_dir()
    assert path.exists()
    assert path.read_text(encoding="utf-8").strip()
