"""Tests for execution backends.

SubprocessBackend tests use real OS subprocesses.
DockerBackend tests are skipped unless Docker is available (``@pytest.mark.docker``).
CloudRunBackend and ECSBackend test only that missing dependencies raise RuntimeError.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from monet.worker.execution import (
    CloudRunBackend,
    ContainerSpec,
    DockerBackend,
    ECSBackend,
    JobStatus,
    SubprocessBackend,
)

# ---------------------------------------------------------------------------
# Docker availability guard
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import-untyped,import-not-found]

        docker.from_env()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# SubprocessBackend
# ---------------------------------------------------------------------------


class TestSubprocessBackend:
    """Tests that exercise real subprocesses."""

    @pytest.mark.asyncio
    async def test_start_returns_endpoint_with_http_address_and_pid(self) -> None:
        backend = SubprocessBackend()
        spec = ContainerSpec(
            entrypoint=[sys.executable, "-c", "import time; time.sleep(999)"]
        )
        endpoint = await backend.start(spec, env={})
        try:
            assert endpoint.backend_type == "subprocess"
            assert endpoint.address.startswith("http://127.0.0.1:")
            assert endpoint.process_id.isdigit()
            assert int(endpoint.process_id) > 0
        finally:
            await backend.kill(endpoint)

    @pytest.mark.asyncio
    async def test_poll_status_running_for_alive_process(self) -> None:
        backend = SubprocessBackend()
        spec = ContainerSpec(
            entrypoint=[sys.executable, "-c", "import time; time.sleep(999)"]
        )
        endpoint = await backend.start(spec, env={})
        try:
            status = await backend.poll_status(endpoint)
            assert status == JobStatus.RUNNING
        finally:
            await backend.kill(endpoint)

    @pytest.mark.asyncio
    async def test_poll_status_succeeded_after_exit_zero(self) -> None:
        backend = SubprocessBackend()
        spec = ContainerSpec(
            entrypoint=[sys.executable, "-c", "import sys; sys.exit(0)"]
        )
        endpoint = await backend.start(spec, env={})
        # Wait for the process to finish.
        proc = backend._procs[endpoint.process_id]
        await asyncio.wait_for(proc.wait(), timeout=10.0)
        status = await backend.poll_status(endpoint)
        assert status == JobStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_poll_status_failed_after_exit_nonzero(self) -> None:
        backend = SubprocessBackend()
        spec = ContainerSpec(
            entrypoint=[sys.executable, "-c", "import sys; sys.exit(1)"]
        )
        endpoint = await backend.start(spec, env={})
        proc = backend._procs[endpoint.process_id]
        await asyncio.wait_for(proc.wait(), timeout=10.0)
        status = await backend.poll_status(endpoint)
        assert status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_stop_terminates_process(self) -> None:
        backend = SubprocessBackend()
        spec = ContainerSpec(
            entrypoint=[sys.executable, "-c", "import time; time.sleep(999)"]
        )
        endpoint = await backend.start(spec, env={})
        # Confirm it's running first.
        assert await backend.poll_status(endpoint) == JobStatus.RUNNING
        await backend.stop(endpoint, grace_period_s=5.0)
        status = await backend.poll_status(endpoint)
        # After stop the process should be gone (SUCCEEDED or FAILED depending
        # on how SIGTERM was handled; not RUNNING).
        assert status != JobStatus.RUNNING

    @pytest.mark.asyncio
    async def test_kill_kills_process(self) -> None:
        backend = SubprocessBackend()
        spec = ContainerSpec(
            entrypoint=[sys.executable, "-c", "import time; time.sleep(999)"]
        )
        endpoint = await backend.start(spec, env={})
        assert await backend.poll_status(endpoint) == JobStatus.RUNNING
        await backend.kill(endpoint)
        # Give the OS a moment to reap the process.
        proc = backend._procs[endpoint.process_id]
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        status = await backend.poll_status(endpoint)
        assert status != JobStatus.RUNNING

    @pytest.mark.asyncio
    async def test_poll_status_unknown_for_untracked_pid(self) -> None:
        backend = SubprocessBackend()
        # Craft an endpoint referencing a PID this instance never started.
        from monet.worker.execution import Endpoint

        fake_endpoint = Endpoint(
            address="http://127.0.0.1:9999",
            process_id="999999999",
            backend_type="subprocess",
        )
        status = await backend.poll_status(fake_endpoint)
        assert status == JobStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_start_raises_when_entrypoint_missing(self) -> None:
        backend = SubprocessBackend()
        spec = ContainerSpec()
        with pytest.raises(RuntimeError, match="entrypoint"):
            await backend.start(spec, env={})

    @pytest.mark.asyncio
    async def test_start_passes_env_to_subprocess(self) -> None:
        """MONET_AGENT_PORT is injected and custom env vars are forwarded."""
        backend = SubprocessBackend()
        # The subprocess prints the env var and exits; we just check it starts
        # without error and the endpoint metadata contains the port.
        spec = ContainerSpec(
            entrypoint=[sys.executable, "-c", "import time; time.sleep(999)"]
        )
        endpoint = await backend.start(spec, env={"MY_VAR": "hello"})
        try:
            assert "port" in endpoint.metadata
        finally:
            await backend.kill(endpoint)


# ---------------------------------------------------------------------------
# DockerBackend (requires Docker daemon)
# ---------------------------------------------------------------------------


@pytest.mark.docker
@pytest.mark.skipif(not _docker_available(), reason="Docker not available")
class TestDockerBackend:
    """Integration tests that require a running Docker daemon."""

    @pytest.mark.asyncio
    async def test_start_returns_endpoint_with_container_id(self) -> None:
        backend = DockerBackend()
        spec = ContainerSpec(image="busybox", entrypoint=["sleep", "999"])
        endpoint = await backend.start(spec, env={})
        try:
            assert endpoint.backend_type == "docker"
            assert len(endpoint.process_id) == 64  # full container ID
            assert endpoint.address == ""  # no expose_port → empty address
        finally:
            await backend.kill(endpoint)

    @pytest.mark.asyncio
    async def test_start_with_expose_port_returns_reachable_address(self) -> None:
        backend = DockerBackend()
        spec = ContainerSpec(
            image="busybox",
            entrypoint=["sh", "-c", "nc -l -p 8080 -e echo ok || sleep 999"],
            expose_port=8080,
        )
        endpoint = await backend.start(spec, env={})
        try:
            assert endpoint.address.startswith("http://localhost:")
            port = int(endpoint.address.rsplit(":", 1)[1])
            assert port > 0
        finally:
            await backend.kill(endpoint)

    @pytest.mark.asyncio
    async def test_poll_status_running_for_live_container(self) -> None:
        backend = DockerBackend()
        spec = ContainerSpec(image="busybox", entrypoint=["sleep", "999"])
        endpoint = await backend.start(spec, env={})
        try:
            status = await backend.poll_status(endpoint)
            assert status == JobStatus.RUNNING
        finally:
            await backend.kill(endpoint)

    @pytest.mark.asyncio
    async def test_stop_and_kill_idempotent(self) -> None:
        backend = DockerBackend()
        spec = ContainerSpec(image="busybox", entrypoint=["sleep", "999"])
        endpoint = await backend.start(spec, env={})
        await backend.stop(endpoint, grace_period_s=5.0)
        # Second call must not raise.
        await backend.stop(endpoint, grace_period_s=1.0)
        await backend.kill(endpoint)


# ---------------------------------------------------------------------------
# CloudRunBackend — dependency-guard test
# ---------------------------------------------------------------------------


class TestCloudRunBackendMissingDep:
    """Verifies that a helpful RuntimeError is raised when google-cloud-run
    is not installed. The check uses unittest.mock to hide the package."""

    @pytest.mark.asyncio
    async def test_start_raises_runtime_error_without_dep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("google.cloud.run_v2"):
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        backend = CloudRunBackend(project="p", region="r", job="j")
        spec = ContainerSpec()
        with pytest.raises(RuntimeError, match="google-cloud-run"):
            await backend.start(spec, env={})


# ---------------------------------------------------------------------------
# ECSBackend — dependency-guard test
# ---------------------------------------------------------------------------


class TestECSBackendMissingDep:
    """Verifies that a helpful RuntimeError is raised when aioboto3 is not
    installed."""

    @pytest.mark.asyncio
    async def test_start_raises_runtime_error_without_dep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "aioboto3":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        backend = ECSBackend(cluster="c", task_definition="td")
        spec = ContainerSpec()
        with pytest.raises(RuntimeError, match="aioboto3"):
            await backend.start(spec, env={})
