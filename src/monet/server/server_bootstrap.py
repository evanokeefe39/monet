"""Server bootstrap + default graph factories for Aegra / LangGraph.

Dual role:

1. **Bootstrap** (runs once on import): load + validate a
   :class:`~monet.config.ServerConfig`, wire tracing, artifacts, the
   task queue, the lazy worker, and the agent manifest. A typo in
   ``MONET_QUEUE_BACKEND`` or a missing ``MONET_API_KEY`` in distributed
   mode fails here — loud — rather than 500-ing on a later request.
   The resolved, redacted config is logged at ``INFO``.

2. **Graph factories**: 0-arg wrappers (``build_default_graph``,
   ``build_chat_graph``, ``build_execution_graph``) for Aegra's loader.
   Point ``aegra.json`` at these to serve the default monet graphs
   with zero configuration.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from monet.artifacts import artifacts_from_env, configure_artifacts
from monet.config import MONET_QUEUE_BACKEND, ConfigError, QueueConfig, ServerConfig
from monet.core.tracing import configure_tracing
from monet.orchestration import (
    build_chat_graph as _build_chat_graph,
)
from monet.orchestration import (
    build_default_graph as _build_default_graph,
)
from monet.orchestration import (
    build_execution_subgraph as _build_execution_subgraph,
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

# Aegra routes stdlib ``logging`` records through structlog, so any
# ``logging.getLogger("monet...").info(...)`` call lands in the same
# server log stream the operator sees on stdout. But the root Python
# logger defaults to WARNING, which silences every INFO-level monet
# log (invoke_agent dispatch, worker claim/complete, chat node
# transitions, etc.). Promote the ``monet`` namespace to INFO at boot
# so operational activity is visible without a ``--verbose`` flag.
# Respect an explicit ``MONET_LOG_LEVEL`` override for debug sessions.
_monet_log_level = os.environ.get("MONET_LOG_LEVEL", "INFO").upper()
logging.getLogger("monet").setLevel(getattr(logging, _monet_log_level, logging.INFO))

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


def build_default_graph() -> StateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    import monet.agents  # noqa: F401 — registers reference agents on first compile only

    return _build_default_graph()


def build_execution_graph() -> StateGraph:  # type: ignore[type-arg]
    """0-arg wrapper exposing the execution subgraph as an invocable graph.

    Drives a pre-approved ``WorkBrief`` (pointer + routing skeleton) through
    the flat-DAG executor without a planning step. Input shape::

        {
            "work_brief_pointer": {"artifact_id": "...", "url": "..."},
            "routing_skeleton": {"goal": "...", "nodes": [...]},
            "run_id": "...",
            "trace_id": "...",
        }

    Scheduled / unattended runs feed frozen briefs through this entrypoint.
    Interactive runs use the compound ``default`` graph so planning + HITL
    still apply.
    """
    import monet.agents  # noqa: F401 — registers reference agents on first compile only

    return _build_execution_subgraph()


__all__ = [
    "build_chat_graph",
    "build_default_graph",
    "build_execution_graph",
    "queue",
]
