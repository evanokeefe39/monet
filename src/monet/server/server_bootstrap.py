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

from monet.artifacts import ArtifactClient, artifacts_from_env, configure_artifacts
from monet.config import (
    MONET_ARTIFACT_BACKEND,
    MONET_QUEUE_BACKEND,
    MONET_QUEUE_CUSTOM_BACKEND,
    ConfigError,
    QueueConfig,
    ServerConfig,
)
from monet.config._resolve import resolve_backend
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
from monet.orchestration import configure_queue, get_queue
from monet.queue import InMemoryTaskQueue, TaskQueue

if TYPE_CHECKING:
    from langgraph.graph import StateGraph

_log = logging.getLogger("monet.server")


def _create_queue(cfg: QueueConfig) -> TaskQueue:
    """Create a task queue from a validated :class:`QueueConfig`.

    ``cfg.validate_for_boot()`` must have been called first; this
    function trusts that credentials for the chosen backend are present.
    """
    if cfg.custom_backend:
        from typing import cast

        return cast(
            "TaskQueue",
            resolve_backend(
                config_ref=cfg.custom_backend,
                env_var_name=MONET_QUEUE_CUSTOM_BACKEND,
                default_factory=lambda: None,  # unreachable — config_ref is set
                protocol=TaskQueue,
            ),
        )
    if cfg.backend == "memory":
        from monet.config import read_int
        from monet.progress.backends.sqlite_store import SqliteProgressStore

        db_path = os.environ.get("MONET_PROGRESS_DB")
        store = None
        if db_path:
            max_ev = read_int("MONET_PROGRESS_MAX_EVENTS", 50_000)
            ttl_days = read_int("MONET_PROGRESS_TTL_DAYS", 7)
            store = SqliteProgressStore(db_path, max_events=max_ev, ttl_days=ttl_days)
            _log.info(
                "Persistent progress store enabled: %s (max=%d, ttl=%dd)",
                db_path,
                max_ev,
                ttl_days,
            )

        return InMemoryTaskQueue(  # type: ignore[no-any-return]
            completion_ttl_seconds=cfg.completion_ttl_seconds, telemetry_store=store
        )

    if cfg.backend == "redis":
        from monet.queue.backends.redis_streams import RedisStreamsTaskQueue

        assert cfg.redis_uri is not None  # validated by cfg.validate_for_boot()
        return RedisStreamsTaskQueue(
            cfg.redis_uri,
            work_stream_maxlen=cfg.work_stream_maxlen,
            pool_size=cfg.redis_pool_size,
            lease_ttl_seconds=cfg.lease_ttl_seconds,
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
configure_artifacts(
    resolve_backend(
        config_ref=_config.artifacts.backend,
        env_var_name=MONET_ARTIFACT_BACKEND,
        default_factory=lambda: artifacts_from_env(default_root=_config.artifacts.root),
        protocol=ArtifactClient,
    )
)


def bootstrap_server() -> TaskQueue:
    """Create and install the process-wide task queue. Idempotent.

    Must be called from the canonical ``_aegra_routes`` lifespan; not
    from module body.  File-path re-executions of this module under
    Aegra's synthetic ``aegra_graphs.*`` namespace do not call this
    function — they only use the 0-arg graph factories below — so the
    queue singleton is never overwritten from a wrong namespace.

    Subsequent calls return the already-configured queue without
    creating a new one or re-logging the boot summary, so tests that
    wire their own queue via ``configure_queue`` then call
    ``bootstrap_server`` transparently receive the existing instance.
    """
    existing = get_queue()
    if existing is not None:
        return existing
    q: TaskQueue = _create_queue(_config.queue)
    configure_queue(q)
    _log.info("monet server booted: %s", _config.redacted_summary())
    return q


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
    return _build_execution_subgraph()


__all__ = [
    "bootstrap_server",
    "build_chat_graph",
    "build_default_graph",
    "build_execution_graph",
]
