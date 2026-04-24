"""Tests for create_unified_app, create_control_app, create_data_app."""

from __future__ import annotations

from monet.queue import InMemoryTaskQueue
from monet.server import create_control_app, create_data_app, create_unified_app
from monet.server._capabilities import CapabilityIndex


def _route_paths(app: object) -> set[str]:
    """Collect all URL paths registered on the FastAPI app."""
    return {route.path for route in app.routes}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# create_control_app
# ---------------------------------------------------------------------------


def test_control_app_has_control_routes() -> None:
    queue = InMemoryTaskQueue()
    cap = CapabilityIndex()
    app = create_control_app(queue, cap)
    paths = _route_paths(app)
    assert "/api/v1/pools/{pool}/claim" in paths
    assert "/api/v1/tasks/{task_id}/complete" in paths
    assert "/api/v1/tasks/{task_id}/fail" in paths
    assert "/api/v1/workers/{worker_id}/heartbeat" in paths
    assert "/api/v1/health" in paths


def test_control_app_excludes_data_routes() -> None:
    queue = InMemoryTaskQueue()
    cap = CapabilityIndex()
    app = create_control_app(queue, cap)
    paths = _route_paths(app)
    assert "/api/v1/runs/{run_id}/events" not in paths
    # Legacy progress query is data-plane only
    assert "/api/v1/runs/{run_id}/progress" not in paths


def test_control_app_no_writer_in_state() -> None:
    queue = InMemoryTaskQueue()
    cap = CapabilityIndex()
    app = create_control_app(queue, cap)
    assert not hasattr(app.state, "progress_writer")
    assert not hasattr(app.state, "progress_reader")


# ---------------------------------------------------------------------------
# create_data_app
# ---------------------------------------------------------------------------


def _make_writer() -> object:
    class _W:
        async def record(self, run_id: str, event: object) -> int:
            return 1

    return _W()


def _make_reader() -> object:
    class _R:
        async def query(self, run_id: str, *, after: int = 0, limit: int = 100) -> list:
            return []

        def stream(self, run_id: str, *, after: int = 0):  # type: ignore[override]
            raise NotImplementedError

        async def has_cause(self, run_id: str, cause_id: str) -> bool:
            return False

    return _R()


def test_data_app_has_data_routes() -> None:
    app = create_data_app(_make_writer(), _make_reader())  # type: ignore[arg-type]
    paths = _route_paths(app)
    assert "/api/v1/runs/{run_id}/events" in paths
    assert "/api/v1/runs/{run_id}/progress" in paths
    assert "/api/v1/health" in paths


def test_data_app_excludes_control_routes() -> None:
    app = create_data_app(_make_writer(), _make_reader())  # type: ignore[arg-type]
    paths = _route_paths(app)
    assert "/api/v1/pools/{pool}/claim" not in paths
    assert "/api/v1/tasks/{task_id}/complete" not in paths
    assert "/api/v1/workers/{worker_id}/heartbeat" not in paths


def test_data_app_wires_writer_reader() -> None:
    w = _make_writer()
    r = _make_reader()
    app = create_data_app(w, r)  # type: ignore[arg-type]
    assert app.state.progress_writer is w
    assert app.state.progress_reader is r


# ---------------------------------------------------------------------------
# create_unified_app
# ---------------------------------------------------------------------------


def test_unified_app_has_both_planes() -> None:
    queue = InMemoryTaskQueue()
    cap = CapabilityIndex()
    app = create_unified_app(queue, cap)
    paths = _route_paths(app)
    # Control
    assert "/api/v1/pools/{pool}/claim" in paths
    assert "/api/v1/workers/{worker_id}/heartbeat" in paths
    # Data
    assert "/api/v1/runs/{run_id}/events" in paths
    assert "/api/v1/runs/{run_id}/progress" in paths


def test_unified_app_wires_writer_when_provided() -> None:
    queue = InMemoryTaskQueue()
    cap = CapabilityIndex()
    w = _make_writer()
    app = create_unified_app(queue, cap, writer=w)  # type: ignore[arg-type]
    assert app.state.progress_writer is w


def test_unified_app_no_writer_when_omitted() -> None:
    queue = InMemoryTaskQueue()
    cap = CapabilityIndex()
    app = create_unified_app(queue, cap)
    assert not hasattr(app.state, "progress_writer")
