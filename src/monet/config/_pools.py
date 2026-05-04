"""Pool topology configuration from monet.toml + environment variables.

Relocated from ``monet.server._config`` so both server and orchestration
can import pool configuration without a circular dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from monet.config._load import default_config_path, read_toml

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["GatewayConfig", "PoolConfig", "load_gateway_config", "load_pool_config"]

_VALID_BACKENDS = frozenset(
    {"in_process", "subprocess", "docker", "kubernetes", "cloudrun", "ecs"}
)
_VALID_WORKLOADS = frozenset({"task", "persistent"})

# Old type values — rejected at boot with migration guidance.
_LEGACY_TYPES = frozenset({"local", "pull", "push"})

_MIGRATION_MSG = (
    "Pool {name!r} uses legacy type={type!r}. Migrate to the new schema:\n"
    "  type='local'  -> backend = 'in_process'\n"
    "  type='pull'   -> backend = 'subprocess' | 'docker' | 'kubernetes'\n"
    "  type='push'   -> backend = 'cloudrun' | 'ecs'\n"
    "See docs/architecture/worker-composition-plan.md for the full migration guide."
)

# Per-backend required TOML fields.
_BACKEND_REQUIRED: dict[str, list[str]] = {
    "cloudrun": ["project", "region", "job"],
    "ecs": ["cluster", "task_definition"],
    "kubernetes": ["namespace", "deployment"],
    "docker": [],
    "subprocess": [],
    "in_process": [],
}


@dataclass(frozen=True)
class PoolConfig:
    """Configuration for a single agent pool.

    Attributes:
        name: Pool identifier matching the ``[pools.<name>]`` key in monet.toml.
        backend: Execution backend — ``in_process``, ``subprocess``, ``docker``,
            ``kubernetes``, ``cloudrun``, or ``ecs``.
        workload: Workload type — ``task`` (per-task backend lifecycle) or
            ``persistent`` (long-running instance shared across tasks).
            Ignored for ``in_process``, ``cloudrun``, and ``ecs`` backends.
        concurrency: Maximum simultaneous tasks for this pool.
        task_timeout_s: Seconds before a task is considered failed.
        lease_ttl: Task lease TTL in seconds.
        image: Container image for docker/cloudrun/ecs backends.
        agent_port: Port the agent listens on inside the container. When set,
            the Docker backend publishes this port to a random host port and
            returns a reachable ``http://localhost:{host_port}`` address.
        warm_pool_size: Number of pre-warmed persistent instances.
        startup_timeout_s: Seconds to wait for a new instance to become ready.
        graceful_shutdown_s: Seconds to wait for a draining instance to finish.
        heartbeat_interval_s: Seconds between instance liveness checks.
        restart_policy: When to restart a failed instance.
        max_restarts: Maximum restarts within ``restart_window_s``.
        restart_window_s: Rolling window for counting restarts.
        backpressure_queue_max: Maximum tasks queued while all instances are busy.
        poll_interval_s: Seconds between cloud API status polls (cloudrun/ecs).
        gateway: Explicit gateway URL for agents in this pool. Overrides worker default.
        namespace: Kubernetes namespace (kubernetes backend only).
        deployment: Kubernetes deployment name (kubernetes backend only).
        project: GCP project ID (cloudrun backend only).
        region: GCP region (cloudrun backend only).
        job: Cloud Run job name (cloudrun backend only).
        cluster: ECS cluster name (ecs backend only).
        task_definition: ECS task definition ARN/name (ecs backend only).
        subnet_ids: ECS VPC subnet IDs (ecs backend only).
        security_groups: ECS security group IDs (ecs backend only).
    """

    name: str
    backend: Literal[
        "in_process", "subprocess", "docker", "kubernetes", "cloudrun", "ecs"
    ]

    workload: Literal["task", "persistent"] = "task"
    concurrency: int = 4
    task_timeout_s: float = 300.0
    lease_ttl: int = 300

    image: str | None = None
    agent_port: int | None = None

    warm_pool_size: int = 0
    startup_timeout_s: float = 30.0
    graceful_shutdown_s: float = 30.0
    heartbeat_interval_s: float = 10.0
    restart_policy: Literal["always", "on_failure", "never"] = "on_failure"
    max_restarts: int = 3
    restart_window_s: float = 300.0
    backpressure_queue_max: int = 10

    poll_interval_s: float = 5.0
    gateway: str | None = None

    namespace: str | None = None
    deployment: str | None = None

    project: str | None = None
    region: str | None = None
    job: str | None = None

    cluster: str | None = None
    task_definition: str | None = None
    subnet_ids: tuple[str, ...] = ()
    security_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class GatewayConfig:
    """Data plane gateway configuration from ``[gateway]`` in monet.toml.

    Attributes:
        port: Port for the embedded gateway in dev mode. Defaults to 2027.
        signing_key_env: Environment variable name holding the JWT signing key.
        tunnel: Optional tunnel provider to auto-start (e.g. ``"cloudflare"``).
    """

    port: int = 2027
    signing_key_env: str = "MONET_GATEWAY_KEY"
    tunnel: str | None = None


def _get_str(
    data: dict[str, object], key: str, default: str | None = None
) -> str | None:
    val = data.get(key, default)
    return str(val) if val is not None else None


def _get_int(data: dict[str, object], key: str, default: int) -> int:
    return int(cast("int", data.get(key, default)))


def _get_float(data: dict[str, object], key: str, default: float) -> float:
    return float(cast("float", data.get(key, default)))


def _get_strtuple(data: dict[str, object], key: str) -> tuple[str, ...]:
    raw = data.get(key, [])
    return tuple(str(v) for v in cast("list[object]", raw))


def load_pool_config(path: Path | None = None) -> dict[str, PoolConfig]:
    """Load pool configuration from monet.toml.

    If *path* is ``None``, resolves ``monet.toml`` from the current working
    directory (or ``MONET_CONFIG_PATH`` env override). If the file does not
    exist, returns a default single-pool configuration with ``in_process`` backend.

    Args:
        path: Explicit path to a ``monet.toml`` file.

    Returns:
        Mapping of pool name to :class:`PoolConfig`.

    Raises:
        ValueError: If a pool uses a legacy type value, an unknown backend, or
            is missing a backend-specific required field.
    """
    resolved = path if path is not None else default_config_path()

    if not resolved.exists():
        return {"local": PoolConfig(name="local", backend="in_process")}

    raw = read_toml(resolved)
    pools_section: dict[str, dict[str, object]] = raw.get("pools", {})
    result: dict[str, PoolConfig] = {}

    for name, pool_data in pools_section.items():
        # Reject legacy type values immediately.
        pool_type = pool_data.get("type")
        if pool_type in _LEGACY_TYPES:
            raise ValueError(_MIGRATION_MSG.format(type=pool_type, name=name))

        backend = pool_data.get("backend")
        if backend not in _VALID_BACKENDS:
            raise ValueError(
                f"Pool {name!r}: invalid backend={backend!r}. "
                f"Must be one of: {', '.join(sorted(_VALID_BACKENDS))}"
            )
        backend = cast(
            'Literal["in_process","subprocess","docker","kubernetes","cloudrun","ecs"]',
            backend,
        )

        workload_raw = pool_data.get("workload", "task")
        if workload_raw not in _VALID_WORKLOADS:
            raise ValueError(
                f"Pool {name!r}: invalid workload={workload_raw!r}. "
                f"Must be one of: {', '.join(sorted(_VALID_WORKLOADS))}"
            )
        workload = cast('Literal["task","persistent"]', workload_raw)

        # Validate backend-specific required fields.
        for req in _BACKEND_REQUIRED.get(str(backend), []):
            if not pool_data.get(req):
                raise ValueError(
                    f"Pool {name!r} with backend={backend!r} requires '{req}'"
                )

        result[name] = PoolConfig(
            name=name,
            backend=backend,
            workload=workload,
            concurrency=_get_int(pool_data, "concurrency", 4),
            task_timeout_s=_get_float(pool_data, "task_timeout_s", 300.0),
            lease_ttl=_get_int(pool_data, "lease_ttl", 300),
            image=_get_str(pool_data, "image"),
            agent_port=_get_int(pool_data, "agent_port", 0) or None,
            warm_pool_size=_get_int(pool_data, "warm_pool_size", 0),
            startup_timeout_s=_get_float(pool_data, "startup_timeout_s", 30.0),
            graceful_shutdown_s=_get_float(pool_data, "graceful_shutdown_s", 30.0),
            heartbeat_interval_s=_get_float(pool_data, "heartbeat_interval_s", 10.0),
            restart_policy=cast(
                'Literal["always","on_failure","never"]',
                pool_data.get("restart_policy", "on_failure"),
            ),
            max_restarts=_get_int(pool_data, "max_restarts", 3),
            restart_window_s=_get_float(pool_data, "restart_window_s", 300.0),
            backpressure_queue_max=_get_int(pool_data, "backpressure_queue_max", 10),
            poll_interval_s=_get_float(pool_data, "poll_interval_s", 5.0),
            gateway=_get_str(pool_data, "gateway"),
            namespace=_get_str(pool_data, "namespace"),
            deployment=_get_str(pool_data, "deployment"),
            project=_get_str(pool_data, "project"),
            region=_get_str(pool_data, "region"),
            job=_get_str(pool_data, "job"),
            cluster=_get_str(pool_data, "cluster"),
            task_definition=_get_str(pool_data, "task_definition"),
            subnet_ids=_get_strtuple(pool_data, "subnet_ids"),
            security_groups=_get_strtuple(pool_data, "security_groups"),
        )

    return result


def load_gateway_config(path: Path | None = None) -> GatewayConfig:
    """Load ``[gateway]`` section from monet.toml.

    Returns defaults if the file does not exist or the section is absent.

    Args:
        path: Explicit path to a ``monet.toml`` file.

    Returns:
        :class:`GatewayConfig` with resolved values.
    """
    resolved = path if path is not None else default_config_path()

    if not resolved.exists():
        return GatewayConfig()

    raw = read_toml(resolved)
    gw: dict[str, object] = raw.get("gateway", {})

    return GatewayConfig(
        port=int(cast("int", gw.get("port", 2027))),
        signing_key_env=str(gw.get("signing_key_env", "MONET_GATEWAY_KEY")),
        tunnel=str(gw["tunnel"]) if gw.get("tunnel") else None,
    )
