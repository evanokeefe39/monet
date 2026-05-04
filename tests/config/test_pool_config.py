"""Tests for pool config parsing and the gateway config loader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from monet.config._pools import (
    load_gateway_config,
    load_pool_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "monet.toml"
    p.write_text(content, encoding="utf-8")
    return p


# ── Default / missing file ────────────────────────────────────────────────────


def test_load_pool_config_no_file_returns_default(tmp_path: Path) -> None:
    pools = load_pool_config(tmp_path / "monet.toml")
    assert "local" in pools
    assert pools["local"].backend == "in_process"


def test_load_gateway_config_no_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load_gateway_config(tmp_path / "monet.toml")
    assert cfg.port == 2027
    assert cfg.signing_key_env == "MONET_GATEWAY_KEY"
    assert cfg.tunnel is None


# ── in_process backend ────────────────────────────────────────────────────────


def test_in_process_pool(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "[pools.local]\nbackend = 'in_process'\n")
    pools = load_pool_config(path)
    assert pools["local"].backend == "in_process"
    assert pools["local"].workload == "task"


# ── subprocess backend ────────────────────────────────────────────────────────


def test_subprocess_pool_defaults(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        "[pools.dev]\nbackend = 'subprocess'\nworkload = 'task'\nconcurrency = 4\n",
    )
    p = load_pool_config(path)["dev"]
    assert p.backend == "subprocess"
    assert p.concurrency == 4
    assert p.task_timeout_s == 300.0


# ── docker backend ────────────────────────────────────────────────────────────


def test_docker_persistent_pool(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        """
[pools.research]
backend = "docker"
workload = "persistent"
image = "registry/img:1.0"
concurrency = 2
warm_pool_size = 2
startup_timeout_s = 30.0
graceful_shutdown_s = 30.0
heartbeat_interval_s = 10.0
restart_policy = "on_failure"
max_restarts = 3
""",
    )
    p = load_pool_config(path)["research"]
    assert p.backend == "docker"
    assert p.workload == "persistent"
    assert p.image == "registry/img:1.0"
    assert p.warm_pool_size == 2
    assert p.restart_policy == "on_failure"


# ── cloudrun backend ──────────────────────────────────────────────────────────


def test_cloudrun_pool(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        """
[pools.burst]
backend = "cloudrun"
project = "my-project"
region = "us-central1"
job = "monet-worker"
task_timeout_s = 300
poll_interval_s = 5
gateway = "https://dp.example.com"
""",
    )
    p = load_pool_config(path)["burst"]
    assert p.backend == "cloudrun"
    assert p.project == "my-project"
    assert p.region == "us-central1"
    assert p.job == "monet-worker"
    assert p.gateway == "https://dp.example.com"
    assert p.poll_interval_s == 5.0


# ── ecs backend ───────────────────────────────────────────────────────────────


def test_ecs_pool(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        """
[pools.fargate]
backend = "ecs"
cluster = "monet-cluster"
task_definition = "monet-worker:3"
subnet_ids = ["subnet-abc", "subnet-def"]
security_groups = ["sg-111"]
""",
    )
    p = load_pool_config(path)["fargate"]
    assert p.backend == "ecs"
    assert p.cluster == "monet-cluster"
    assert p.task_definition == "monet-worker:3"
    assert p.subnet_ids == ("subnet-abc", "subnet-def")
    assert p.security_groups == ("sg-111",)


# ── kubernetes backend ────────────────────────────────────────────────────────


def test_kubernetes_pool(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        """
[pools.k8s]
backend = "kubernetes"
namespace = "monet"
deployment = "openclaw"
concurrency = 8
""",
    )
    p = load_pool_config(path)["k8s"]
    assert p.backend == "kubernetes"
    assert p.namespace == "monet"
    assert p.deployment == "openclaw"


# ── Validation errors ─────────────────────────────────────────────────────────


def test_legacy_type_local_rejected(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "[pools.old]\ntype = 'local'\n")
    with pytest.raises(ValueError, match="legacy"):
        load_pool_config(path)


def test_legacy_type_pull_rejected(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "[pools.old]\ntype = 'pull'\n")
    with pytest.raises(ValueError, match="Migrate"):
        load_pool_config(path)


def test_legacy_type_push_rejected(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "[pools.old]\ntype = 'push'\n")
    with pytest.raises(ValueError, match="backend"):
        load_pool_config(path)


def test_unknown_backend_rejected(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "[pools.bad]\nbackend = 'lambda'\n")
    with pytest.raises(ValueError, match="invalid backend"):
        load_pool_config(path)


def test_invalid_workload_rejected(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path, "[pools.bad]\nbackend = 'subprocess'\nworkload = 'streaming'\n"
    )
    with pytest.raises(ValueError, match="invalid workload"):
        load_pool_config(path)


def test_cloudrun_missing_project_rejected(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        "[pools.burst]\nbackend = 'cloudrun'\nregion = 'us-central1'\njob = 'j'\n",
    )
    with pytest.raises(ValueError, match="project"):
        load_pool_config(path)


def test_ecs_missing_cluster_rejected(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        "[pools.fargate]\nbackend = 'ecs'\ntask_definition = 'td:1'\n",
    )
    with pytest.raises(ValueError, match="cluster"):
        load_pool_config(path)


def test_kubernetes_missing_namespace_rejected(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        "[pools.k8s]\nbackend = 'kubernetes'\ndeployment = 'app'\n",
    )
    with pytest.raises(ValueError, match="namespace"):
        load_pool_config(path)


# ── Gateway config ────────────────────────────────────────────────────────────


def test_gateway_config_parsed(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        "[gateway]\nport = 3000\nsigning_key_env = 'MY_KEY'\ntunnel = 'cloudflare'\n",
    )
    cfg = load_gateway_config(path)
    assert cfg.port == 3000
    assert cfg.signing_key_env == "MY_KEY"
    assert cfg.tunnel == "cloudflare"


def test_gateway_config_missing_section_returns_defaults(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "[pools.local]\nbackend = 'in_process'\n")
    cfg = load_gateway_config(path)
    assert cfg.port == 2027
    assert cfg.tunnel is None


# ── WorkerConfig.pools ────────────────────────────────────────────────────────


def test_worker_config_pools_derives_from_pool() -> None:
    from monet.config._schema._worker import WorkerConfig

    cfg = WorkerConfig(pool="gpu")
    assert cfg.pools == ["gpu"]


def test_worker_config_explicit_pools_not_overridden() -> None:
    from monet.config._schema._worker import WorkerConfig

    cfg = WorkerConfig(pool="local", pools=["gpu", "cpu"])
    assert cfg.pools == ["gpu", "cpu"]
