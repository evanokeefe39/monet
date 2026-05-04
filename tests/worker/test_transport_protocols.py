"""Tests for worker transport and execution protocol types."""

from __future__ import annotations

import time

import pytest

from monet.worker.execution._protocol import (
    ContainerSpec,
    Endpoint,
    ExecutionBackend,
    JobStatus,
)
from monet.worker.transport._errors import AgentError, ProtocolError, TransportError
from monet.worker.transport._protocol import ObservedEvent, Session, TransportAdapter

# ── ObservedEvent ─────────────────────────────────────────────────────────────


def test_observed_event_is_frozen() -> None:
    ev = ObservedEvent(type="result", data={"output": "ok"})
    with pytest.raises((AttributeError, TypeError)):
        ev.type = "other"  # type: ignore[misc]


def test_observed_event_timestamp_auto() -> None:
    before = time.monotonic()
    ev = ObservedEvent(type="result", data={})
    after = time.monotonic()
    assert before <= ev.timestamp <= after


def test_observed_event_explicit_timestamp() -> None:
    ev = ObservedEvent(type="transport_metric", data={}, timestamp=1.0)
    assert ev.timestamp == 1.0


# ── Error hierarchy ───────────────────────────────────────────────────────────


def test_transport_error_is_exception() -> None:
    err = TransportError("connection refused")
    assert isinstance(err, Exception)
    assert str(err) == "connection refused"


def test_protocol_error_is_exception() -> None:
    err = ProtocolError("bad json")
    assert isinstance(err, Exception)


def test_agent_error_is_exception() -> None:
    err = AgentError("agent returned 500")
    assert isinstance(err, Exception)


def test_error_types_are_distinct() -> None:
    assert not issubclass(TransportError, ProtocolError)
    assert not issubclass(ProtocolError, AgentError)
    assert not issubclass(AgentError, TransportError)


# ── Protocol runtime checks ───────────────────────────────────────────────────


def test_session_is_runtime_checkable() -> None:
    # A plain object does not satisfy Session.
    assert not isinstance(object(), Session)


def test_transport_adapter_is_runtime_checkable() -> None:
    assert not isinstance(object(), TransportAdapter)


def test_execution_backend_is_runtime_checkable() -> None:
    assert not isinstance(object(), ExecutionBackend)


# ── Endpoint ──────────────────────────────────────────────────────────────────


def test_endpoint_is_frozen() -> None:
    ep = Endpoint(
        address="http://localhost:8080", process_id="123", backend_type="subprocess"
    )
    with pytest.raises((AttributeError, TypeError)):
        ep.address = "other"  # type: ignore[misc]


def test_endpoint_metadata_defaults_empty() -> None:
    ep = Endpoint(address="http://x", process_id="1", backend_type="docker")
    assert ep.metadata == {}


def test_endpoint_metadata_is_mutable() -> None:
    """Metadata dict is not frozen — callers can build it incrementally."""
    meta: dict[str, object] = {}
    ep = Endpoint(
        address="http://x", process_id="1", backend_type="docker", metadata=meta
    )
    meta["port"] = 8080
    assert ep.metadata["port"] == 8080


# ── ContainerSpec ─────────────────────────────────────────────────────────────


def test_container_spec_all_optional() -> None:
    spec = ContainerSpec()
    assert spec.image is None
    assert spec.entrypoint is None
    assert spec.expose_port is None
    assert spec.cpu is None
    assert spec.memory is None
    assert spec.labels == {}


def test_container_spec_is_frozen() -> None:
    spec = ContainerSpec(image="my-image:1.0")
    with pytest.raises((AttributeError, TypeError)):
        spec.image = "other"  # type: ignore[misc]


# ── JobStatus ─────────────────────────────────────────────────────────────────


def test_job_status_values() -> None:
    assert JobStatus.RUNNING.value == "running"
    assert JobStatus.SUCCEEDED.value == "succeeded"
    assert JobStatus.FAILED.value == "failed"
    assert JobStatus.UNKNOWN.value == "unknown"


def test_job_status_from_value() -> None:
    assert JobStatus("running") is JobStatus.RUNNING
    assert JobStatus("succeeded") is JobStatus.SUCCEEDED
