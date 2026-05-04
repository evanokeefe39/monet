"""Tests for worker workload execution.

Uses lightweight fakes for Session, ExecutionBackend, and TaskQueue rather
than hitting real subprocesses or Docker daemons.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monet.worker.execution._protocol import ContainerSpec, Endpoint, JobStatus
from monet.worker.transport._errors import AgentError, ProtocolError
from monet.worker.transport._protocol import ObservedEvent
from monet.worker.workload import (
    ContainerSupervisor,
    ManagedInstance,
    TaskFailure,
    TaskRouter,
    execute_cloud_push_workload,
    execute_managed_workload,
    execute_persistent_workload,
)
from monet.worker.workload._collect import (
    _build_agent_result,
    _collect,
    _run_with_lease,
    _task_env,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _fake_endpoint(pid: str = "123") -> Endpoint:
    return Endpoint(
        address="http://127.0.0.1:9999",
        process_id=pid,
        backend_type="subprocess",
    )


def _fake_task_record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "task_id": "t-001",
        "agent_id": "my-agent",
        "command": "run",
        "pool": "local",
        "context": {"run_id": "r-001"},
        "status": "claimed",
        "result": None,
        "created_at": "2026-01-01T00:00:00Z",
        "claimed_at": "2026-01-01T00:00:01Z",
        "completed_at": None,
    }
    base.update(overrides)
    return base


def _fake_pool_config(**overrides: Any) -> Any:
    cfg = MagicMock()
    cfg.name = overrides.get("name", "local")
    cfg.backend = overrides.get("backend", "subprocess")
    cfg.workload = overrides.get("workload", "task")
    cfg.image = overrides.get("image")
    cfg.warm_pool_size = overrides.get("warm_pool_size", 0)
    cfg.startup_timeout_s = overrides.get("startup_timeout_s", 5.0)
    cfg.graceful_shutdown_s = overrides.get("graceful_shutdown_s", 5.0)
    cfg.task_timeout_s = overrides.get("task_timeout_s", 30.0)
    cfg.poll_interval_s = overrides.get("poll_interval_s", 0.01)
    cfg.restart_window_s = overrides.get("restart_window_s", 300.0)
    cfg.max_restarts = overrides.get("max_restarts", 3)
    return cfg


class FakeSession:
    """Minimal Session fake."""

    def __init__(self, events: list[ObservedEvent]) -> None:
        self._events = events
        self.submitted: list[dict[str, Any]] = []
        self.cancelled = False
        self.closed = False

    async def submit(self, payload: dict[str, Any]) -> None:
        self.submitted.append(payload)

    async def receive(self):  # type: ignore[return]
        for event in self._events:
            yield event

    async def cancel(self) -> None:
        self.cancelled = True

    async def close(self) -> None:
        self.closed = True


class FakeBackend:
    """Minimal ExecutionBackend fake."""

    def __init__(
        self,
        start_endpoint: Endpoint | None = None,
        poll_sequence: list[JobStatus] | None = None,
    ) -> None:
        self._endpoint = start_endpoint or _fake_endpoint()
        self._poll_sequence = poll_sequence or [JobStatus.RUNNING]
        self._poll_idx = 0
        self.started = 0
        self.stopped = 0
        self.killed = 0

    async def start(self, spec: ContainerSpec, env: dict[str, str]) -> Endpoint:
        self.started += 1
        return self._endpoint

    async def poll_status(self, endpoint: Endpoint) -> JobStatus:
        idx = min(self._poll_idx, len(self._poll_sequence) - 1)
        status = self._poll_sequence[idx]
        self._poll_idx += 1
        return status

    async def stop(self, endpoint: Endpoint, grace_period_s: float) -> None:
        self.stopped += 1

    async def kill(self, endpoint: Endpoint) -> None:
        self.killed += 1


class FakeQueue:
    """Minimal TaskQueue fake (no QueueMaintenance)."""

    def __init__(self) -> None:
        self.completed: dict[str, Any] = {}
        self.failed: dict[str, str] = {}

    async def complete(self, task_id: str, result: Any) -> None:
        self.completed[task_id] = result

    async def fail(self, task_id: str, error: str) -> None:
        self.failed[task_id] = error


# ---------------------------------------------------------------------------
# _collect helpers
# ---------------------------------------------------------------------------


class TestTaskEnv:
    def test_populates_standard_keys(self) -> None:
        record = _fake_task_record()
        env = _task_env(record)  # type: ignore[arg-type]
        assert env["MONET_TASK_ID"] == "t-001"
        assert env["MONET_AGENT_ID"] == "my-agent"
        assert env["MONET_COMMAND"] == "run"
        assert env["MONET_POOL"] == "local"
        assert env["MONET_RUN_ID"] == "r-001"

    def test_missing_run_id_defaults_to_empty(self) -> None:
        record = _fake_task_record(context={})
        env = _task_env(record)  # type: ignore[arg-type]
        assert env["MONET_RUN_ID"] == ""

    def test_none_context_defaults_to_empty(self) -> None:
        record = _fake_task_record(context=None)
        env = _task_env(record)  # type: ignore[arg-type]
        assert env["MONET_RUN_ID"] == ""


class TestBuildAgentResult:
    def test_success_result(self) -> None:
        raw = {"success": True, "output": "hello", "artifacts": [], "signals": []}
        result = _build_agent_result(raw)
        assert result.success is True
        assert result.output == "hello"
        assert result.artifacts == ()
        assert result.signals == ()

    def test_with_artifact_pointer(self) -> None:
        raw = {
            "success": True,
            "artifacts": [
                {"artifact_id": "a1", "url": "s3://bucket/a1", "key": "report"}
            ],
            "signals": [],
        }
        result = _build_agent_result(raw)
        assert len(result.artifacts) == 1
        assert result.artifacts[0]["artifact_id"] == "a1"
        assert result.artifacts[0].get("key") == "report"

    def test_with_signal(self) -> None:
        raw = {
            "success": False,
            "signals": [
                {"type": "low_quality", "reason": "score < 0.5", "metadata": None}
            ],
        }
        result = _build_agent_result(raw)
        assert len(result.signals) == 1
        assert result.signals[0]["type"] == "low_quality"

    def test_empty_dict_defaults(self) -> None:
        result = _build_agent_result({})
        assert result.success is False
        assert result.output is None


class TestCollect:
    @pytest.mark.asyncio
    async def test_returns_data_on_result_event(self) -> None:
        events = [
            ObservedEvent(type="transport_metric", data={"latency": 0.1}),
            ObservedEvent(type="result", data={"success": True, "output": "done"}),
        ]
        session = FakeSession(events)
        data = await _collect(session)
        assert data == {"success": True, "output": "done"}

    @pytest.mark.asyncio
    async def test_raises_protocol_error_when_no_result(self) -> None:
        session = FakeSession([ObservedEvent(type="transport_metric", data={})])
        with pytest.raises(ProtocolError):
            await _collect(session)


class TestRunWithLease:
    @pytest.mark.asyncio
    async def test_returns_result(self) -> None:
        events = [ObservedEvent(type="result", data={"success": True})]
        session = FakeSession(events)
        queue = FakeQueue()
        result = await _run_with_lease(session, queue, "t-1", timeout_s=5.0)  # type: ignore[arg-type]
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_raises_timeout(self) -> None:
        async def _slow_receive():
            await asyncio.sleep(10)
            yield ObservedEvent(type="result", data={})

        session = MagicMock()
        session.receive = _slow_receive
        queue = FakeQueue()
        with pytest.raises(TimeoutError):
            await _run_with_lease(session, queue, "t-1", timeout_s=0.05)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_no_lease_renewal_for_plain_queue(self) -> None:
        """_renew_lease parks when queue lacks QueueMaintenance — should not raise."""
        events = [ObservedEvent(type="result", data={"success": True})]
        session = FakeSession(events)
        queue = FakeQueue()
        result = await _run_with_lease(session, queue, "t-1", timeout_s=5.0)  # type: ignore[arg-type]
        assert result["success"] is True


# ---------------------------------------------------------------------------
# execute_managed_workload
# ---------------------------------------------------------------------------


class TestExecuteManagedWorkload:
    def _fake_agent(self) -> Any:
        agent = MagicMock()
        agent.transport.cmd = None
        return agent

    @pytest.mark.asyncio
    async def test_success_path(self) -> None:
        backend = FakeBackend(poll_sequence=[JobStatus.RUNNING])
        events = [ObservedEvent(type="result", data={"success": True, "output": "ok"})]
        session = FakeSession(events)
        transport = AsyncMock()
        transport.connect.return_value = session
        pool = _fake_pool_config()
        queue = FakeQueue()

        result = await execute_managed_workload(
            record=_fake_task_record(),  # type: ignore[arg-type]
            agent=self._fake_agent(),
            pool=pool,
            backend=backend,  # type: ignore[arg-type]
            transport_factory=transport,
            queue=queue,  # type: ignore[arg-type]
            gateway_env={
                "MONET_GATEWAY_URL": "http://localhost:2027",
                "MONET_TOKEN": "tok",
            },
        )

        assert result.success is True
        assert result.output == "ok"
        assert backend.started == 1
        assert backend.stopped == 1
        assert session.closed is True

    @pytest.mark.asyncio
    async def test_agent_error_raises_task_failure(self) -> None:
        backend = FakeBackend(poll_sequence=[JobStatus.RUNNING])

        async def _receive_error():
            raise AgentError("agent crashed")
            yield  # make it a generator

        session = MagicMock()
        session.submit = AsyncMock()
        session.receive = _receive_error
        session.cancel = AsyncMock()
        session.close = AsyncMock()
        transport = AsyncMock()
        transport.connect.return_value = session

        with pytest.raises(TaskFailure, match="agent crashed"):
            await execute_managed_workload(
                record=_fake_task_record(),  # type: ignore[arg-type]
                agent=self._fake_agent(),
                pool=_fake_pool_config(),
                backend=backend,  # type: ignore[arg-type]
                transport_factory=transport,
                queue=FakeQueue(),  # type: ignore[arg-type]
                gateway_env={},
            )

        assert backend.stopped == 1

    @pytest.mark.asyncio
    async def test_stop_called_on_startup_failure(self) -> None:
        # Backend starts but immediately reports FAILED
        backend = FakeBackend(poll_sequence=[JobStatus.FAILED])
        transport = AsyncMock()
        pool = _fake_pool_config(startup_timeout_s=0.1)

        with pytest.raises(RuntimeError, match="exited during startup"):
            await execute_managed_workload(
                record=_fake_task_record(),  # type: ignore[arg-type]
                agent=self._fake_agent(),
                pool=pool,
                backend=backend,  # type: ignore[arg-type]
                transport_factory=transport,
                queue=FakeQueue(),  # type: ignore[arg-type]
                gateway_env={},
            )

        assert backend.stopped == 1

    @pytest.mark.asyncio
    async def test_timeout_cancels_session_and_stops_backend(self) -> None:
        backend = FakeBackend(poll_sequence=[JobStatus.RUNNING])

        async def _slow_receive():
            await asyncio.sleep(10)
            yield ObservedEvent(type="result", data={})

        session = MagicMock()
        session.submit = AsyncMock()
        session.receive = _slow_receive
        session.cancel = AsyncMock()
        session.close = AsyncMock()
        transport = AsyncMock()
        transport.connect.return_value = session

        with pytest.raises(TaskFailure, match="deadline exceeded"):
            await execute_managed_workload(
                record=_fake_task_record(),  # type: ignore[arg-type]
                agent=self._fake_agent(),
                pool=_fake_pool_config(task_timeout_s=0.05),
                backend=backend,  # type: ignore[arg-type]
                transport_factory=transport,
                queue=FakeQueue(),  # type: ignore[arg-type]
                gateway_env={},
            )

        session.cancel.assert_awaited_once()
        session.close.assert_awaited_once()
        assert backend.stopped == 1


# ---------------------------------------------------------------------------
# execute_persistent_workload
# ---------------------------------------------------------------------------


class TestExecutePersistentWorkload:
    @pytest.mark.asyncio
    async def test_success_acquires_releases(self) -> None:
        endpoint = _fake_endpoint()
        instance = ManagedInstance(pool="p1", endpoint=endpoint, state="idle")
        pool_config = _fake_pool_config(name="p1", task_timeout_s=10.0)
        router = TaskRouter({"p1": pool_config})
        router.add_instance("p1", instance)

        events = [
            ObservedEvent(type="result", data={"success": True, "output": "done"})
        ]
        session = FakeSession(events)
        transport = AsyncMock()
        transport.connect.return_value = session

        result = await execute_persistent_workload(
            record=_fake_task_record(),  # type: ignore[arg-type]
            pool_name="p1",
            router=router,
            transport_factory=transport,
            queue=FakeQueue(),  # type: ignore[arg-type]
        )

        assert result.success is True
        assert instance.state == "idle"  # released

    @pytest.mark.asyncio
    async def test_returns_task_failure_when_no_instances(self) -> None:
        pool_config = _fake_pool_config(name="p1")
        router = TaskRouter({"p1": pool_config})

        with pytest.raises(TaskFailure, match="draining or all instances dead"):
            await execute_persistent_workload(
                record=_fake_task_record(),  # type: ignore[arg-type]
                pool_name="p1",
                router=router,
                transport_factory=AsyncMock(),
                queue=FakeQueue(),  # type: ignore[arg-type]
            )

    @pytest.mark.asyncio
    async def test_instance_released_on_agent_error(self) -> None:
        endpoint = _fake_endpoint()
        instance = ManagedInstance(pool="p1", endpoint=endpoint, state="idle")
        pool_config = _fake_pool_config(name="p1", task_timeout_s=10.0)
        router = TaskRouter({"p1": pool_config})
        router.add_instance("p1", instance)

        async def _receive_error():
            raise AgentError("boom")
            yield  # make it a generator

        session = MagicMock()
        session.submit = AsyncMock()
        session.receive = _receive_error
        session.cancel = AsyncMock()
        session.close = AsyncMock()
        transport = AsyncMock()
        transport.connect.return_value = session

        with pytest.raises(TaskFailure):
            await execute_persistent_workload(
                record=_fake_task_record(),  # type: ignore[arg-type]
                pool_name="p1",
                router=router,
                transport_factory=transport,
                queue=FakeQueue(),  # type: ignore[arg-type]
            )

        assert instance.state == "idle"  # released even on error


# ---------------------------------------------------------------------------
# execute_cloud_push_workload
# ---------------------------------------------------------------------------


class TestExecuteCloudPushWorkload:
    @pytest.mark.asyncio
    async def test_success_polls_then_retrieves_result(self) -> None:
        backend = FakeBackend(
            poll_sequence=[JobStatus.RUNNING, JobStatus.RUNNING, JobStatus.SUCCEEDED]
        )
        pool = _fake_pool_config(poll_interval_s=0.01)

        gateway_result = {
            "success": True,
            "output": "cloud-done",
            "artifacts": [],
            "signals": [],
        }

        with patch(
            "monet.worker.workload._persistent._retrieve_result_from_gateway",
            new=AsyncMock(return_value=gateway_result),
        ):
            result = await execute_cloud_push_workload(
                record=_fake_task_record(),  # type: ignore[arg-type]
                pool=pool,
                backend=backend,  # type: ignore[arg-type]
                queue=FakeQueue(),  # type: ignore[arg-type]
                gateway_url="http://localhost:2027",
                token="tok",
            )

        assert result.success is True
        assert result.output == "cloud-done"
        assert backend.started == 1

    @pytest.mark.asyncio
    async def test_cloud_job_failure_raises_task_failure(self) -> None:
        backend = FakeBackend(poll_sequence=[JobStatus.RUNNING, JobStatus.FAILED])
        pool = _fake_pool_config(poll_interval_s=0.01)

        with pytest.raises(TaskFailure, match="cloud job failed"):
            await execute_cloud_push_workload(
                record=_fake_task_record(),  # type: ignore[arg-type]
                pool=pool,
                backend=backend,  # type: ignore[arg-type]
                queue=FakeQueue(),  # type: ignore[arg-type]
                gateway_url="http://localhost:2027",
                token="tok",
            )


# ---------------------------------------------------------------------------
# TaskRouter
# ---------------------------------------------------------------------------


class TestTaskRouter:
    def _router_with_pool(self, **pool_kwargs: Any) -> tuple[TaskRouter, Any]:
        pool = _fake_pool_config(**pool_kwargs)
        router = TaskRouter({pool.name: pool})
        return router, pool

    @pytest.mark.asyncio
    async def test_acquire_idle_returns_instance_and_marks_busy(self) -> None:
        router, _pool = self._router_with_pool(name="p")
        inst = ManagedInstance(pool="p", endpoint=_fake_endpoint(), state="idle")
        router.add_instance("p", inst)

        acquired = await router.acquire_idle("p")
        assert acquired is inst
        assert inst.state == "busy"

    @pytest.mark.asyncio
    async def test_acquire_returns_none_for_dead_pool(self) -> None:
        router, _pool = self._router_with_pool(name="p")
        inst = ManagedInstance(pool="p", endpoint=_fake_endpoint(), state="dead")
        router.add_instance("p", inst)

        result = await asyncio.wait_for(router.acquire_idle("p"), timeout=0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_acquire_returns_none_when_draining(self) -> None:
        router, _pool = self._router_with_pool(name="p")
        inst = ManagedInstance(pool="p", endpoint=_fake_endpoint(), state="idle")
        router.add_instance("p", inst)
        router.set_draining("p", draining=True)

        result = await asyncio.wait_for(router.acquire_idle("p"), timeout=0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_release_returns_instance_to_idle(self) -> None:
        router, _pool = self._router_with_pool(name="p")
        inst = ManagedInstance(pool="p", endpoint=_fake_endpoint(), state="busy")
        router.add_instance("p", inst)

        await router.release("p", inst)
        assert inst.state == "idle"

    @pytest.mark.asyncio
    async def test_acquire_blocks_until_release(self) -> None:
        router, _pool = self._router_with_pool(name="p", task_timeout_s=30.0)
        inst = ManagedInstance(pool="p", endpoint=_fake_endpoint(), state="busy")
        router.add_instance("p", inst)

        async def _release_after_delay() -> None:
            await asyncio.sleep(0.05)
            await router.release("p", inst)

        _bg = asyncio.create_task(_release_after_delay())  # noqa: RUF006
        acquired = await asyncio.wait_for(router.acquire_idle("p"), timeout=1.0)
        assert acquired is inst

    def test_has_capacity_true_when_idle(self) -> None:
        router, _pool = self._router_with_pool(name="p")
        inst = ManagedInstance(pool="p", endpoint=_fake_endpoint(), state="idle")
        router.add_instance("p", inst)
        assert router.has_capacity("p") is True

    def test_has_capacity_false_when_all_busy(self) -> None:
        router, _pool = self._router_with_pool(name="p")
        inst = ManagedInstance(pool="p", endpoint=_fake_endpoint(), state="busy")
        router.add_instance("p", inst)
        assert router.has_capacity("p") is False

    def test_task_timeout_s_from_pool_config(self) -> None:
        router, _pool = self._router_with_pool(name="p", task_timeout_s=120.0)
        assert router.task_timeout_s("p") == 120.0

    def test_task_timeout_s_default_for_unknown_pool(self) -> None:
        router = TaskRouter({})
        assert router.task_timeout_s("unknown") == 300.0


# ---------------------------------------------------------------------------
# ContainerSupervisor
# ---------------------------------------------------------------------------


class TestContainerSupervisor:
    @pytest.mark.asyncio
    async def test_start_pool_starts_warm_instances(self) -> None:
        backend = FakeBackend()
        config = _fake_pool_config(warm_pool_size=2)
        supervisor = ContainerSupervisor()
        instances = await supervisor.start_pool("p", config, backend, env={})  # type: ignore[arg-type]

        assert len(instances) == 2
        assert all(i.state == "idle" for i in instances)
        assert backend.started == 2

    @pytest.mark.asyncio
    async def test_check_liveness_running_returns_true(self) -> None:
        backend = FakeBackend(poll_sequence=[JobStatus.RUNNING])
        supervisor = ContainerSupervisor()
        config = _fake_pool_config(warm_pool_size=0)
        await supervisor.start_pool("p", config, backend, env={})  # type: ignore[arg-type]

        inst = ManagedInstance(pool="p", endpoint=_fake_endpoint(), state="idle")
        alive = await supervisor.check_liveness(inst)
        assert alive is True
        assert inst.state == "idle"

    @pytest.mark.asyncio
    async def test_check_liveness_failed_marks_dead(self) -> None:
        backend = FakeBackend(poll_sequence=[JobStatus.FAILED])
        supervisor = ContainerSupervisor()
        config = _fake_pool_config(warm_pool_size=0)
        await supervisor.start_pool("p", config, backend, env={})  # type: ignore[arg-type]

        inst = ManagedInstance(pool="p", endpoint=_fake_endpoint(), state="idle")
        alive = await supervisor.check_liveness(inst)
        assert alive is False
        assert inst.state == "dead"

    @pytest.mark.asyncio
    async def test_restart_instance_returns_new_instance(self) -> None:
        backend = FakeBackend()
        config = _fake_pool_config(
            max_restarts=3, restart_window_s=300.0, warm_pool_size=0
        )
        supervisor = ContainerSupervisor()
        await supervisor.start_pool("p", config, backend, env={})  # type: ignore[arg-type]

        dead = ManagedInstance(pool="p", endpoint=_fake_endpoint("old"), state="dead")
        new_inst = await supervisor.restart_instance("p", dead)
        assert new_inst is not None
        assert new_inst.state == "idle"
        assert backend.started == 1  # start_pool had 0 warm; restart added 1

    @pytest.mark.asyncio
    async def test_restart_circuit_opens_after_max_restarts(self) -> None:
        backend = FakeBackend()
        config = _fake_pool_config(
            max_restarts=2, restart_window_s=300.0, warm_pool_size=0
        )
        supervisor = ContainerSupervisor()
        await supervisor.start_pool("p", config, backend, env={})  # type: ignore[arg-type]

        dead = ManagedInstance(pool="p", endpoint=_fake_endpoint("old"), state="dead")
        # Two restarts succeed.
        r1 = await supervisor.restart_instance("p", dead)
        r2 = await supervisor.restart_instance("p", dead)
        assert r1 is not None
        assert r2 is not None
        # Third restart: circuit open.
        r3 = await supervisor.restart_instance("p", dead)
        assert r3 is None

    @pytest.mark.asyncio
    async def test_drain_waits_for_busy_then_stops(self) -> None:
        backend = FakeBackend(poll_sequence=[JobStatus.RUNNING] * 10)
        config = _fake_pool_config(
            warm_pool_size=1, graceful_shutdown_s=0.0, task_timeout_s=30.0
        )
        supervisor = ContainerSupervisor()
        instances = await supervisor.start_pool("p", config, backend, env={})  # type: ignore[arg-type]

        router = TaskRouter({"p": config})
        for inst in instances:
            router.add_instance("p", inst)

        # Mark one instance busy, then release it after a short delay.
        inst = instances[0]
        inst.state = "busy"

        async def _release() -> None:
            await asyncio.sleep(0.05)
            await router.release("p", inst)

        _bg = asyncio.create_task(_release())  # noqa: RUF006
        await asyncio.wait_for(supervisor.drain("p", router), timeout=2.0)

        assert router.is_draining("p") is True
        assert backend.stopped >= 1

    @pytest.mark.asyncio
    async def test_reconcile_orphans_returns_zero(self) -> None:
        supervisor = ContainerSupervisor()
        result = await supervisor.reconcile_orphans("p", "worker-abc")
        assert result == 0
