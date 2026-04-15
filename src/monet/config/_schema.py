"""Typed configuration schemas per deployable unit.

Each deployable unit (server, worker, client, CLI-dev, orchestration
dispatcher, artifact store, observability) has its own pydantic schema
composed from the small per-concern models below:

- :class:`ObservabilityConfig` — tracing targets.
- :class:`ArtifactsConfig` — artifact store root + distributed flag.
- :class:`QueueConfig` — task queue backend + credentials.
- :class:`AuthConfig` — bearer-token secret.
- :class:`OrchestrationConfig` — dispatch-side tuning (agent timeout).
- :class:`ServerConfig` — composes everything a server process needs.
- :class:`WorkerConfig` — what a worker process needs to claim + execute.
- :class:`ClientConfig` — what :class:`monet.client.MonetClient` needs.
- :class:`CLIDevConfig` — what ``monet dev`` / ``monet run`` require.

Each schema exposes:

- ``load()`` — classmethod that reads env + ``monet.toml`` and returns a
  populated instance. Raises :exc:`ConfigError` only on parse failures
  (malformed value); missing values fall through to defaults.
- ``validate_for_boot()`` — runs cross-field preconditions that must
  hold before a process can start. Raises :exc:`ConfigError` naming the
  first violation. Called once at process startup — never per-request.
- ``redacted_summary()`` — returns a dict suitable for INFO-level boot
  logging. Secrets are shown as ``"set"`` / ``"unset"`` but never as
  their raw value.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — pydantic needs this at runtime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .._ports import STANDARD_DEV_PORT, STANDARD_LANGFUSE_PORT
from ._env import (
    EXA_API_KEY,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    HONEYCOMB_API_KEY,
    HONEYCOMB_DATASET,
    LANGFUSE_HOST,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGSMITH_API_KEY,
    LANGSMITH_PROJECT,
    MONET_AGENT_TIMEOUT,
    MONET_API_KEY,
    MONET_ARTIFACTS_DIR,
    MONET_DISTRIBUTED,
    MONET_QUEUE_BACKEND,
    MONET_SERVER_URL,
    MONET_TRACE_FILE,
    MONET_WORKER_AGENTS,
    MONET_WORKER_CONCURRENCY,
    MONET_WORKER_HEARTBEAT_INTERVAL,
    MONET_WORKER_POLL_INTERVAL,
    MONET_WORKER_POOL,
    MONET_WORKER_SHUTDOWN_TIMEOUT,
    OTEL_EXPORTER_OTLP_ENDPOINT,
    OTEL_EXPORTER_OTLP_HEADERS,
    OTEL_SERVICE_NAME,
    REDIS_URI,
    TAVILY_API_KEY,
    ConfigError,
    read_bool,
    read_enum,
    read_float,
    read_int,
    read_path,
    read_str,
)

__all__ = [
    "ArtifactsConfig",
    "AuthConfig",
    "CLIDevConfig",
    "ClientConfig",
    "ObservabilityConfig",
    "OrchestrationConfig",
    "QueueBackend",
    "QueueConfig",
    "ServerConfig",
    "WorkerConfig",
]


QueueBackend = Literal["memory", "redis"]
_QUEUE_BACKENDS: tuple[QueueBackend, ...] = (
    "memory",
    "redis",
)

_SECRET = "set"
_UNSET = "unset"

_DEFAULT_SERVER_URL = f"http://localhost:{STANDARD_DEV_PORT}"
_DEFAULT_LANGFUSE_HOST = f"http://localhost:{STANDARD_LANGFUSE_PORT}"


def _redact(value: str | None) -> str:
    return _SECRET if value else _UNSET


# --- Observability --------------------------------------------------------


class ObservabilityConfig(BaseModel):
    """Tracing configuration.

    Resolves OTLP endpoint and headers from three vendor shortcuts
    (Langfuse, LangSmith, Honeycomb) without mutating ``os.environ``.
    Use :meth:`otlp_endpoint_and_headers` to get the final values to
    hand to an OTel exporter.
    """

    model_config = ConfigDict(frozen=True)

    service_name: str = "monet"
    trace_file: Path | None = None
    otlp_endpoint: str | None = None
    otlp_headers: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = _DEFAULT_LANGFUSE_HOST
    langsmith_api_key: str | None = None
    langsmith_project: str | None = None
    honeycomb_api_key: str | None = None
    honeycomb_dataset: str | None = None

    @classmethod
    def load(cls) -> ObservabilityConfig:
        return cls(
            service_name=read_str(OTEL_SERVICE_NAME, "monet") or "monet",
            trace_file=read_path(MONET_TRACE_FILE),
            otlp_endpoint=read_str(OTEL_EXPORTER_OTLP_ENDPOINT),
            otlp_headers=read_str(OTEL_EXPORTER_OTLP_HEADERS),
            langfuse_public_key=read_str(LANGFUSE_PUBLIC_KEY),
            langfuse_secret_key=read_str(LANGFUSE_SECRET_KEY),
            langfuse_host=(
                read_str(LANGFUSE_HOST, _DEFAULT_LANGFUSE_HOST)
                or _DEFAULT_LANGFUSE_HOST
            ),
            langsmith_api_key=read_str(LANGSMITH_API_KEY),
            langsmith_project=read_str(LANGSMITH_PROJECT),
            honeycomb_api_key=read_str(HONEYCOMB_API_KEY),
            honeycomb_dataset=read_str(HONEYCOMB_DATASET),
        )

    def otlp_endpoint_and_headers(self) -> tuple[str | None, str | None]:
        """Resolve final OTLP endpoint + headers from vendor shortcuts.

        Precedence: explicit ``OTEL_EXPORTER_OTLP_ENDPOINT`` wins; then
        Langfuse if public+secret keys are present; then Honeycomb if
        its API key is present; then LangSmith. Returns ``(None, None)``
        when no target is configured.
        """
        if self.otlp_endpoint:
            return self.otlp_endpoint, self.otlp_headers

        if self.langfuse_public_key and self.langfuse_secret_key:
            import base64

            host = self.langfuse_host.rstrip("/")
            endpoint = f"{host}/api/public/otel"
            token = base64.b64encode(
                f"{self.langfuse_public_key}:{self.langfuse_secret_key}".encode()
            ).decode()
            return endpoint, f"Authorization=Basic {token}"

        if self.honeycomb_api_key:
            headers = f"x-honeycomb-team={self.honeycomb_api_key}"
            if self.honeycomb_dataset:
                headers += f",x-honeycomb-dataset={self.honeycomb_dataset}"
            return "https://api.honeycomb.io", headers

        if self.langsmith_api_key:
            headers = f"x-api-key={self.langsmith_api_key}"
            if self.langsmith_project:
                headers += f",Langsmith-Project={self.langsmith_project}"
            return "https://api.smith.langchain.com/otel", headers

        return None, None

    def otlp_headers_dict(self) -> dict[str, str] | None:
        """Return OTLP headers as a dict suitable for OTLPSpanExporter.

        Parses the comma-separated ``key=value`` form that OTel uses for
        the ``OTEL_EXPORTER_OTLP_HEADERS`` variable. Returns ``None``
        when no headers are configured so callers can pass the value
        straight through to the exporter constructor.
        """
        _, headers = self.otlp_endpoint_and_headers()
        if not headers:
            return None
        pairs: dict[str, str] = {}
        for part in headers.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                pairs[k.strip()] = v.strip()
        return pairs or None

    def redacted_summary(self) -> dict[str, Any]:
        endpoint, _ = self.otlp_endpoint_and_headers()
        return {
            "service_name": self.service_name,
            "trace_file": str(self.trace_file) if self.trace_file else _UNSET,
            "otlp_endpoint": endpoint or _UNSET,
            "langfuse": _redact(self.langfuse_public_key and self.langfuse_secret_key),
            "langsmith": _redact(self.langsmith_api_key),
            "honeycomb": _redact(self.honeycomb_api_key),
        }


# --- Artifacts ------------------------------------------------------------


class ArtifactsConfig(BaseModel):
    """Artifact store root + distributed-mode flag."""

    model_config = ConfigDict(frozen=True)

    root: Path | None = None
    distributed: bool = False

    @classmethod
    def load(cls) -> ArtifactsConfig:
        return cls(
            root=read_path(MONET_ARTIFACTS_DIR),
            distributed=read_bool(MONET_DISTRIBUTED, default=False),
        )

    def resolve_root(self, default: Path) -> Path:
        """Return the effective artifact root, falling back to *default*."""
        return self.root if self.root is not None else default

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "root": str(self.root) if self.root else _UNSET,
            "distributed": self.distributed,
        }


# --- Queue ----------------------------------------------------------------


class QueueConfig(BaseModel):
    """Task queue backend + credentials.

    ``validate_for_boot`` is where we fail an operator-visible error on
    typos like ``MONET_QUEUE_BACKEND=redi`` or missing credentials.
    Memory backend is for tests and single-process development only; it
    is rejected at boot whenever ``REDIS_URI`` is set, which guarantees
    that a deployed server cannot silently drop to in-memory storage.
    """

    model_config = ConfigDict(frozen=True)

    backend: QueueBackend = "memory"
    redis_uri: str | None = None
    work_stream_maxlen: int | None = None
    redis_pool_size: int = 20
    push_dispatch_timeout: float = 10.0

    @classmethod
    def load(cls) -> QueueConfig:
        backend_raw = read_enum(MONET_QUEUE_BACKEND, _QUEUE_BACKENDS, default="memory")
        backend: QueueBackend = (
            backend_raw if backend_raw is not None else "memory"  # type: ignore[assignment]
        )
        return cls(
            backend=backend,
            redis_uri=read_str(REDIS_URI),
        )

    def validate_for_boot(self) -> None:
        if self.backend == "redis" and not self.redis_uri:
            raise ConfigError(
                REDIS_URI,
                None,
                f"a Redis URI (required when {MONET_QUEUE_BACKEND}=redis)",
            )
        if self.backend == "memory" and self.redis_uri:
            raise ConfigError(
                MONET_QUEUE_BACKEND,
                self.backend,
                "the memory backend to be disabled when REDIS_URI is set "
                f"(set {MONET_QUEUE_BACKEND}=redis or unset REDIS_URI)",
            )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "redis_uri": _redact(self.redis_uri),
            "work_stream_maxlen": self.work_stream_maxlen,
            "redis_pool_size": self.redis_pool_size,
            "push_dispatch_timeout": self.push_dispatch_timeout,
        }


# --- Auth -----------------------------------------------------------------


class AuthConfig(BaseModel):
    """Bearer-token secret for the FastAPI server."""

    model_config = ConfigDict(frozen=True)

    api_key: str | None = None

    @classmethod
    def load(cls) -> AuthConfig:
        return cls(api_key=read_str(MONET_API_KEY))

    def validate_for_boot(self, *, required: bool = False) -> None:
        """Validate the bearer token.

        When *required* is ``True`` (typically distributed/production
        mode), a missing key raises :exc:`ConfigError` at boot — this
        prevents a server from booting green and 500-ing on the first
        authenticated call.
        """
        if required and not self.api_key:
            raise ConfigError(
                MONET_API_KEY,
                None,
                "a non-empty bearer token (required when the server is "
                "enforcing auth — set it in the environment before boot)",
            )

    def redacted_summary(self) -> dict[str, Any]:
        return {"api_key": _redact(self.api_key)}


# --- Orchestration --------------------------------------------------------


class OrchestrationConfig(BaseModel):
    """Dispatcher-side tuning. Today just the agent-timeout poll."""

    model_config = ConfigDict(frozen=True)

    agent_timeout: float = Field(default=600.0, gt=0.0)

    @classmethod
    def load(cls) -> OrchestrationConfig:
        timeout = read_float(MONET_AGENT_TIMEOUT, default=600.0)
        if timeout <= 0.0:
            raise ConfigError(
                MONET_AGENT_TIMEOUT,
                str(timeout),
                "a positive float (seconds)",
            )
        return cls(agent_timeout=timeout)

    def redacted_summary(self) -> dict[str, Any]:
        return {"agent_timeout": self.agent_timeout}


# --- Server (composite) ---------------------------------------------------


class ServerConfig(BaseModel):
    """Full config surface consumed by a server process.

    Composes :class:`AuthConfig`, :class:`QueueConfig`,
    :class:`ArtifactsConfig`, :class:`ObservabilityConfig`, and
    :class:`OrchestrationConfig`.
    """

    model_config = ConfigDict(frozen=True)

    auth: AuthConfig
    queue: QueueConfig
    artifacts: ArtifactsConfig
    observability: ObservabilityConfig
    orchestration: OrchestrationConfig

    @classmethod
    def load(cls) -> ServerConfig:
        return cls(
            auth=AuthConfig.load(),
            queue=QueueConfig.load(),
            artifacts=ArtifactsConfig.load(),
            observability=ObservabilityConfig.load(),
            orchestration=OrchestrationConfig.load(),
        )

    def validate_for_boot(self) -> None:
        """Validate preconditions that must hold before the server starts.

        Raises :exc:`ConfigError` on the first violation. The
        :class:`AuthConfig` check is strict only in distributed mode —
        local monolith dev boots don't need a bearer token to be useful,
        but a production distributed server with no ``MONET_API_KEY``
        would boot green and 500 on the first authenticated call, which
        is exactly the silent-failure class this module exists to
        prevent.
        """
        self.auth.validate_for_boot(required=self.artifacts.distributed)
        self.queue.validate_for_boot()

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "auth": self.auth.redacted_summary(),
            "queue": self.queue.redacted_summary(),
            "artifacts": self.artifacts.redacted_summary(),
            "observability": self.observability.redacted_summary(),
            "orchestration": self.orchestration.redacted_summary(),
        }


# --- Worker ---------------------------------------------------------------


class WorkerConfig(BaseModel):
    """Config surface for a :command:`monet worker` process.

    When ``server_url`` is set the worker runs in remote/distributed mode
    and requires ``api_key``. When it is unset, the worker runs in local
    sidecar mode and neither is required.
    """

    model_config = ConfigDict(frozen=True)

    pool: str = "local"
    concurrency: int = Field(default=10, gt=0)
    server_url: str | None = None
    api_key: str | None = None
    agents_toml: Path | None = None
    poll_interval: float = Field(default=0.1, gt=0.0)
    shutdown_timeout: float = Field(default=30.0, gt=0.0)
    heartbeat_interval: float = Field(default=30.0, gt=0.0)
    required_llm_keys: tuple[str, ...] = ()

    @classmethod
    def load(cls) -> WorkerConfig:
        return cls(
            pool=read_str(MONET_WORKER_POOL, "local") or "local",
            concurrency=read_int(MONET_WORKER_CONCURRENCY, default=10),
            server_url=read_str(MONET_SERVER_URL),
            api_key=read_str(MONET_API_KEY),
            agents_toml=read_path(MONET_WORKER_AGENTS),
            poll_interval=read_float(MONET_WORKER_POLL_INTERVAL, default=0.1),
            shutdown_timeout=read_float(MONET_WORKER_SHUTDOWN_TIMEOUT, default=30.0),
            heartbeat_interval=read_float(
                MONET_WORKER_HEARTBEAT_INTERVAL, default=30.0
            ),
        )

    def with_required_llm_keys(self, keys: tuple[str, ...]) -> WorkerConfig:
        """Return a copy carrying the LLM-key names this worker needs."""
        return self.model_copy(update={"required_llm_keys": keys})

    def validate_for_boot(self) -> None:
        if self.server_url and not self.api_key:
            raise ConfigError(
                MONET_API_KEY,
                None,
                f"a non-empty bearer token (required when {MONET_SERVER_URL} is set)",
            )
        if self.required_llm_keys:
            import os as _os

            if not any(_os.environ.get(k) for k in self.required_llm_keys):
                raise ConfigError(
                    " or ".join(self.required_llm_keys),
                    None,
                    "at least one LLM provider key set in the worker "
                    f"environment (e.g. {GEMINI_API_KEY} or "
                    f"{GROQ_API_KEY})",
                )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "pool": self.pool,
            "concurrency": self.concurrency,
            "server_url": self.server_url or _UNSET,
            "api_key": _redact(self.api_key),
            "agents_toml": (str(self.agents_toml) if self.agents_toml else _UNSET),
            "poll_interval": self.poll_interval,
            "shutdown_timeout": self.shutdown_timeout,
            "heartbeat_interval": self.heartbeat_interval,
            "required_llm_keys": list(self.required_llm_keys),
        }


# --- Client ---------------------------------------------------------------


class ClientConfig(BaseModel):
    """Config surface for :class:`monet.client.MonetClient`."""

    model_config = ConfigDict(frozen=True)

    server_url: str = _DEFAULT_SERVER_URL
    api_key: str | None = None

    @classmethod
    def load(cls) -> ClientConfig:
        return cls(
            server_url=(
                read_str(MONET_SERVER_URL, _DEFAULT_SERVER_URL) or _DEFAULT_SERVER_URL
            ),
            api_key=read_str(MONET_API_KEY),
        )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "server_url": self.server_url,
            "api_key": _redact(self.api_key),
        }


# --- CLI dev --------------------------------------------------------------


class CLIDevConfig(BaseModel):
    """What ``monet dev`` / ``monet run`` / ``monet chat`` require.

    The current contract is that at least one LLM provider key is set so
    the reference agents can instantiate a model. The exact key names are
    a policy of the reference agents, not of monet itself; keeping this
    here avoids scattering the check across CLI commands.
    """

    model_config = ConfigDict(frozen=True)

    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    exa_api_key: str | None = None
    tavily_api_key: str | None = None

    @classmethod
    def load(cls) -> CLIDevConfig:
        return cls(
            gemini_api_key=read_str(GEMINI_API_KEY),
            groq_api_key=read_str(GROQ_API_KEY),
            exa_api_key=read_str(EXA_API_KEY),
            tavily_api_key=read_str(TAVILY_API_KEY),
        )

    @model_validator(mode="after")
    def _at_least_one_llm_key_is_informational(self) -> CLIDevConfig:
        # No validation here — the boot check is in validate_for_boot.
        # This hook only exists so future cross-field rules have a home.
        return self

    def validate_for_boot(self) -> None:
        if not (self.gemini_api_key or self.groq_api_key):
            raise ConfigError(
                f"{GEMINI_API_KEY} or {GROQ_API_KEY}",
                None,
                "at least one LLM provider key (set it in .env or the "
                "environment before running monet dev/run/chat)",
            )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "gemini_api_key": _redact(self.gemini_api_key),
            "groq_api_key": _redact(self.groq_api_key),
            "exa_api_key": _redact(self.exa_api_key),
            "tavily_api_key": _redact(self.tavily_api_key),
        }
