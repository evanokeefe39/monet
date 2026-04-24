"""Central registry of environment variables and typed accessors.

This module is the single place in the SDK that reads ``os.environ``. Every
other module consumes config by importing a name constant from here and
calling one of the typed accessors, or by loading a schema from
:mod:`monet.config`.

The rules are:

- Every ``MONET_*`` fixed env var name lives here as a ``Final[str]``
  constant. Patterned names (``MONET_GRAPH_{ROLE}``,
  ``MONET_POOL_{NAME}_URL``) are composed through helper functions so the
  pattern is named once.
- Each accessor returns a validated, typed value or raises
  :exc:`ConfigError` naming the variable, the received value, and the
  expected format. No accessor silently falls back on a malformed value.
  Missing (``""`` or unset) uses the caller's default; malformed fails loud.

The goal is Jidoka: a single typo in configuration fails at the boundary
with an actionable message instead of propagating as a silent fallback.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "EXA_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "HONEYCOMB_API_KEY",
    "HONEYCOMB_DATASET",
    "LANGFUSE_HOST",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "MONET_AGENT_TIMEOUT",
    "MONET_API_KEY",
    "MONET_ARTIFACTS_DIR",
    "MONET_CHAT_BORDER_COLOR",
    "MONET_CHAT_GRAPH",
    "MONET_CHAT_PULSE",
    "MONET_CHAT_RESPOND_MODEL",
    "MONET_CHAT_TRIAGE_MODEL",
    "MONET_CONFIG_PATH",
    "MONET_DISTRIBUTED",
    "MONET_ENV_VARS",
    "MONET_QUEUE_BACKEND",
    "MONET_QUEUE_COMPLETION_TTL",
    "MONET_QUEUE_LEASE_TTL",
    "MONET_QUEUE_RECLAIM_INTERVAL",
    "MONET_SERVER_URL",
    "MONET_SKIP_SMOKE_TEST",
    "MONET_TRACE_FILE",
    "MONET_WORKER_AGENTS",
    "MONET_WORKER_CONCURRENCY",
    "MONET_WORKER_HEARTBEAT_INTERVAL",
    "MONET_WORKER_POLL_INTERVAL",
    "MONET_WORKER_POOL",
    "MONET_WORKER_SHUTDOWN_TIMEOUT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_SERVICE_NAME",
    "REDIS_URI",
    "TAVILY_API_KEY",
    "ConfigError",
    "agent_model",
    "agent_model_env",
    "dispatch_secret_env",
    "graph_role_env",
    "pool_auth_env",
    "pool_url_env",
    "read_bool",
    "read_enum",
    "read_float",
    "read_int",
    "read_path",
    "read_str",
]


class ConfigError(ValueError):
    """Raised when an env var or config value is missing or malformed.

    Attributes:
        var: Name of the environment variable or config key.
        received: Raw value observed. ``None`` for missing.
        expected: Short description of the expected format.
    """

    def __init__(self, var: str, received: str | None, expected: str) -> None:
        self.var = var
        self.received = received
        self.expected = expected
        seen = "unset" if received is None else repr(received)
        super().__init__(f"Invalid value for {var}: got {seen}, expected {expected}.")


# --- Monet-owned fixed env var names --------------------------------------

MONET_API_KEY: Final[str] = "MONET_API_KEY"
MONET_SERVER_URL: Final[str] = "MONET_SERVER_URL"
MONET_SKIP_SMOKE_TEST: Final[str] = "MONET_SKIP_SMOKE_TEST"
MONET_CONFIG_PATH: Final[str] = "MONET_CONFIG_PATH"
MONET_ARTIFACTS_DIR: Final[str] = "MONET_ARTIFACTS_DIR"
MONET_DISTRIBUTED: Final[str] = "MONET_DISTRIBUTED"
MONET_AGENT_TIMEOUT: Final[str] = "MONET_AGENT_TIMEOUT"
MONET_QUEUE_BACKEND: Final[str] = "MONET_QUEUE_BACKEND"
MONET_QUEUE_COMPLETION_TTL: Final[str] = "MONET_QUEUE_COMPLETION_TTL"
MONET_QUEUE_LEASE_TTL: Final[str] = "MONET_QUEUE_LEASE_TTL"
MONET_QUEUE_RECLAIM_INTERVAL: Final[str] = "MONET_QUEUE_RECLAIM_INTERVAL"
MONET_TRACE_FILE: Final[str] = "MONET_TRACE_FILE"
MONET_WORKER_POOL: Final[str] = "MONET_WORKER_POOL"
MONET_WORKER_CONCURRENCY: Final[str] = "MONET_WORKER_CONCURRENCY"
MONET_WORKER_AGENTS: Final[str] = "MONET_WORKER_AGENTS"
MONET_WORKER_POLL_INTERVAL: Final[str] = "MONET_WORKER_POLL_INTERVAL"
MONET_WORKER_SHUTDOWN_TIMEOUT: Final[str] = "MONET_WORKER_SHUTDOWN_TIMEOUT"
MONET_WORKER_HEARTBEAT_INTERVAL: Final[str] = "MONET_WORKER_HEARTBEAT_INTERVAL"
MONET_CHAT_BORDER_COLOR: Final[str] = "MONET_CHAT_BORDER_COLOR"
MONET_CHAT_GRAPH: Final[str] = "MONET_CHAT_GRAPH"
MONET_CHAT_PULSE: Final[str] = "MONET_CHAT_PULSE"
MONET_CHAT_RESPOND_MODEL: Final[str] = "MONET_CHAT_RESPOND_MODEL"
MONET_CHAT_TRIAGE_MODEL: Final[str] = "MONET_CHAT_TRIAGE_MODEL"
MONET_PROGRESS_DB: Final[str] = "MONET_PROGRESS_DB"
MONET_PROGRESS_MAX_EVENTS: Final[str] = "MONET_PROGRESS_MAX_EVENTS"
MONET_PROGRESS_TTL_DAYS: Final[str] = "MONET_PROGRESS_TTL_DAYS"

#: Every fixed ``MONET_*`` name registered above. The test isolation
#: fixture delenv's each of these between tests; the env-vars docs
#: generator iterates this tuple.
MONET_ENV_VARS: Final[tuple[str, ...]] = (
    MONET_API_KEY,
    MONET_SERVER_URL,
    MONET_SKIP_SMOKE_TEST,
    MONET_CONFIG_PATH,
    MONET_ARTIFACTS_DIR,
    MONET_DISTRIBUTED,
    MONET_AGENT_TIMEOUT,
    MONET_QUEUE_BACKEND,
    MONET_QUEUE_COMPLETION_TTL,
    MONET_QUEUE_LEASE_TTL,
    MONET_QUEUE_RECLAIM_INTERVAL,
    MONET_TRACE_FILE,
    MONET_WORKER_POOL,
    MONET_WORKER_CONCURRENCY,
    MONET_WORKER_AGENTS,
    MONET_WORKER_POLL_INTERVAL,
    MONET_WORKER_SHUTDOWN_TIMEOUT,
    MONET_WORKER_HEARTBEAT_INTERVAL,
    MONET_CHAT_BORDER_COLOR,
    MONET_CHAT_GRAPH,
    MONET_CHAT_PULSE,
    MONET_CHAT_RESPOND_MODEL,
    MONET_CHAT_TRIAGE_MODEL,
    MONET_PROGRESS_DB,
    MONET_PROGRESS_MAX_EVENTS,
    MONET_PROGRESS_TTL_DAYS,
)


# --- Patterned env var names ----------------------------------------------


def graph_role_env(role: str) -> str:
    """Return the env var name that overrides a graph role mapping."""
    return f"MONET_GRAPH_{role.upper()}"


def pool_url_env(pool: str) -> str:
    """Return the env var name that supplies a pool's dispatch URL."""
    return f"MONET_POOL_{pool.upper()}_URL"


def pool_auth_env(pool: str) -> str:
    """Return the env var name that supplies a pool's bearer token."""
    return f"MONET_POOL_{pool.upper()}_AUTH"


def dispatch_secret_env(pool: str) -> str:
    """Return the env var name that supplies a push pool's dispatch secret.

    The dispatch secret protects the push worker's ``POST /dispatch``
    endpoint so random internet traffic cannot trigger jobs. It is
    separate from ``MONET_API_KEY``, which protects worker → server
    traffic (progress, complete, fail callbacks).
    """
    return f"MONET_POOL_{pool.upper()}_DISPATCH_SECRET"


def agent_model_env(agent: str) -> str:
    """Return the env var name that overrides a reference agent's model."""
    return f"MONET_{agent.upper()}_MODEL"


def agent_model(agent: str, default: str) -> str:
    """Resolve a reference agent's model string from env, with a default.

    Centralises the ``MONET_<AGENT>_MODEL`` lookup so the registered
    env-var name comes from a single source and the agents do not each
    re-roll the same ``os.environ.get`` call.
    """
    return os.environ.get(agent_model_env(agent)) or default


# --- External vendor names that monet also reads --------------------------

REDIS_URI: Final[str] = "REDIS_URI"
LANGFUSE_PUBLIC_KEY: Final[str] = "LANGFUSE_PUBLIC_KEY"
LANGFUSE_SECRET_KEY: Final[str] = "LANGFUSE_SECRET_KEY"
LANGFUSE_HOST: Final[str] = "LANGFUSE_HOST"
LANGSMITH_API_KEY: Final[str] = "LANGSMITH_API_KEY"
LANGSMITH_PROJECT: Final[str] = "LANGSMITH_PROJECT"
HONEYCOMB_API_KEY: Final[str] = "HONEYCOMB_API_KEY"
HONEYCOMB_DATASET: Final[str] = "HONEYCOMB_DATASET"
OTEL_EXPORTER_OTLP_ENDPOINT: Final[str] = "OTEL_EXPORTER_OTLP_ENDPOINT"
OTEL_EXPORTER_OTLP_HEADERS: Final[str] = "OTEL_EXPORTER_OTLP_HEADERS"
OTEL_SERVICE_NAME: Final[str] = "OTEL_SERVICE_NAME"
GEMINI_API_KEY: Final[str] = "GEMINI_API_KEY"
GROQ_API_KEY: Final[str] = "GROQ_API_KEY"
EXA_API_KEY: Final[str] = "EXA_API_KEY"
TAVILY_API_KEY: Final[str] = "TAVILY_API_KEY"


# --- Accessors ------------------------------------------------------------

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def read_str(name: str, default: str | None = None) -> str | None:
    """Return the raw string value of an env var.

    Empty strings are treated as unset and the default is returned.
    """
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    return raw


def read_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean env var.

    Accepts ``1``/``0``, ``true``/``false``, ``yes``/``no``, ``on``/``off``
    case-insensitively. Unset or empty returns ``default``. Any other
    value raises :exc:`ConfigError`.
    """
    raw = os.environ.get(name, "").strip().lower()
    if raw == "":
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(
        name,
        raw,
        "one of 1/0, true/false, yes/no, on/off (case-insensitive)",
    )


def read_float(name: str, default: float) -> float:
    """Parse a float env var. Malformed values raise :exc:`ConfigError`."""
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(name, raw, "a float") from None


def read_int(name: str, default: int) -> int:
    """Parse an integer env var. Malformed values raise :exc:`ConfigError`."""
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(name, raw, "an integer") from None


def read_path(name: str, default: Path | None = None) -> Path | None:
    """Parse a filesystem path env var. Empty string is treated as unset."""
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    return Path(raw)


def read_enum(
    name: str,
    choices: Iterable[str],
    default: str | None = None,
) -> str | None:
    """Parse an enum-valued env var.

    Values outside ``choices`` raise :exc:`ConfigError` listing the full
    set of valid options so an operator can spot a typo immediately.
    """
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    valid = tuple(choices)
    if raw not in valid:
        raise ConfigError(name, raw, f"one of {{{', '.join(valid)}}}")
    return raw
