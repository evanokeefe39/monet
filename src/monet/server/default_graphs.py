"""Default graph exports for Aegra / LangGraph dev servers.

Point ``aegra.json`` (or ``langgraph.json``) at this module to serve the
four monet graphs with zero configuration.

Importing this module performs one-shot server bootstrap: load +
validate a :class:`~monet.config.ServerConfig`, then wire tracing,
artifacts, and the task queue. A typo in ``MONET_QUEUE_BACKEND`` or a
missing ``MONET_API_KEY`` in distributed mode fails here — loud — rather
than 500-ing on a later request. The resolved, redacted config is
logged at ``INFO`` so an operator can see what the running process
actually picked up.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import monet.agents  # noqa: F401 — registers reference agents
from monet.artifacts import artifacts_from_env, configure_artifacts
from monet.config import MONET_QUEUE_BACKEND, ConfigError, QueueConfig, ServerConfig
from monet.core.tracing import configure_tracing
from monet.orchestration import (
    build_chat_graph as _build_chat_graph,
)
from monet.orchestration import (
    build_entry_subgraph as _build_entry_subgraph,
)
from monet.orchestration import (
    build_execution_subgraph as _build_execution_subgraph,
)
from monet.orchestration import (
    build_planning_subgraph as _build_planning_subgraph,
)
from monet.orchestration import (
    configure_queue,
)
from monet.queue import InMemoryTaskQueue, TaskQueue
from monet.server import configure_lazy_worker

if TYPE_CHECKING:
    from langgraph.graph import StateGraph

_log = logging.getLogger("monet.server")


def _create_queue(cfg: QueueConfig) -> TaskQueue:
    """Create a task queue from a validated :class:`QueueConfig`.

    ``cfg.validate_for_boot()`` must have been called first; this
    function trusts that credentials for the chosen backend are present.
    """
    if cfg.backend == "memory":
        queue: TaskQueue = InMemoryTaskQueue()
        return queue
    if cfg.backend == "redis":
        from monet.queue.backends.redis_streams import RedisStreamsTaskQueue

        assert cfg.redis_uri is not None  # validated by cfg.validate_for_boot()
        return RedisStreamsTaskQueue(
            cfg.redis_uri,
            work_stream_maxlen=cfg.work_stream_maxlen,
            pool_size=cfg.redis_pool_size,
        )
    raise ConfigError(
        MONET_QUEUE_BACKEND,
        cfg.backend,
        "one of {memory, redis}",
    )


# ── Server bootstrap (runs at import time) ─────────────────────────────
_config = ServerConfig.load()
_config.validate_for_boot()

configure_tracing(_config.observability)

if not _config.artifacts.distributed:
    configure_artifacts(artifacts_from_env(default_root=_config.artifacts.root))

queue: TaskQueue = _create_queue(_config.queue)
configure_queue(queue)
configure_lazy_worker(queue)

# Wire the agent manifest handle so `invoke_agent` in a graph can read
# pool assignments populated by remote worker registration. Without
# this, `get_agent_manifest()` returns a handle whose `is_configured()`
# is False and `invoke_agent` falls back to pool="local", which breaks
# pool-based routing for split-fleet deployments.
from monet.agent_manifest import configure_agent_manifest  # noqa: E402
from monet.core.manifest import default_manifest  # noqa: E402

configure_agent_manifest(default_manifest)

_log.info("monet server booted: %s", _config.redacted_summary())


# Aegra's factory classifier inspects parameter count: a 1-arg function
# whose parameter isn't ServerRuntime is treated as a config-accepting
# factory and called with a RunnableConfig dict. The real graph
# builders accept an optional ``hooks`` kwarg, which Aegra would
# misinterpret. Wrap them as 0-arg functions so Aegra calls them once
# at load time with no arguments — the default ``hooks=None`` is what
# we want here.


def build_chat_graph() -> StateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    return _build_chat_graph()


def build_entry_graph() -> StateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    return _build_entry_subgraph()


def build_planning_graph() -> StateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    return _build_planning_subgraph()


def build_execution_graph() -> StateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    return _build_execution_subgraph()


__all__ = [
    "build_chat_graph",
    "build_entry_graph",
    "build_execution_graph",
    "build_planning_graph",
    "queue",
]
