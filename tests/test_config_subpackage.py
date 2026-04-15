"""Tests for the central configuration subpackage.

Covers:

- Every accessor in :mod:`monet.config._env` on valid, missing, and
  malformed inputs.
- Each schema's ``load`` and ``validate_for_boot``.
- ``redacted_summary`` never leaks a raw secret.
- The autouse env-isolation fixture actually isolates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from monet.config import (
    EXA_API_KEY,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    MONET_AGENT_TIMEOUT,
    MONET_API_KEY,
    MONET_ARTIFACTS_DIR,
    MONET_DISTRIBUTED,
    MONET_ENV_VARS,
    MONET_QUEUE_BACKEND,
    MONET_SERVER_URL,
    MONET_WORKER_CONCURRENCY,
    REDIS_URI,
    ArtifactsConfig,
    AuthConfig,
    CLIDevConfig,
    ClientConfig,
    ConfigError,
    ObservabilityConfig,
    OrchestrationConfig,
    QueueConfig,
    ServerConfig,
    WorkerConfig,
    agent_model_env,
    default_config_path,
    graph_role_env,
    pool_auth_env,
    pool_url_env,
    read_bool,
    read_enum,
    read_float,
    read_int,
    read_path,
    read_str,
    read_toml,
    read_toml_section,
)

if TYPE_CHECKING:
    from pathlib import Path


# ── Accessors ──────────────────────────────────────────────────────────


def test_read_str_missing_returns_default() -> None:
    assert read_str("MONET_UNDEFINED_NAME", "fallback") == "fallback"


def test_read_str_empty_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_API_KEY, "")
    assert read_str(MONET_API_KEY, "fallback") == "fallback"


def test_read_str_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_API_KEY, "secret")
    assert read_str(MONET_API_KEY) == "secret"


def test_read_bool_accepts_common_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    for val in ("1", "true", "TRUE", "yes", "on", "On"):
        monkeypatch.setenv(MONET_DISTRIBUTED, val)
        assert read_bool(MONET_DISTRIBUTED) is True


def test_read_bool_accepts_common_falsy(monkeypatch: pytest.MonkeyPatch) -> None:
    for val in ("0", "false", "no", "off"):
        monkeypatch.setenv(MONET_DISTRIBUTED, val)
        assert read_bool(MONET_DISTRIBUTED) is False


def test_read_bool_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_DISTRIBUTED, "banana")
    with pytest.raises(ConfigError) as exc_info:
        read_bool(MONET_DISTRIBUTED)
    assert exc_info.value.var == MONET_DISTRIBUTED
    assert exc_info.value.received == "banana"


def test_read_float_missing_returns_default() -> None:
    assert read_float("MONET_UNDEFINED", 3.14) == 3.14


def test_read_float_malformed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_AGENT_TIMEOUT, "abc")
    with pytest.raises(ConfigError) as exc_info:
        read_float(MONET_AGENT_TIMEOUT, 600.0)
    assert exc_info.value.var == MONET_AGENT_TIMEOUT


def test_read_int_missing_returns_default() -> None:
    assert read_int("MONET_UNDEFINED", 42) == 42


def test_read_int_malformed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_WORKER_CONCURRENCY, "lots")
    with pytest.raises(ConfigError):
        read_int(MONET_WORKER_CONCURRENCY, 10)


def test_read_path_missing_returns_default() -> None:
    assert read_path("MONET_UNDEFINED") is None


def test_read_path_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(MONET_ARTIFACTS_DIR, str(tmp_path))
    result = read_path(MONET_ARTIFACTS_DIR)
    assert result is not None
    assert str(result) == str(tmp_path)


def test_read_enum_rejects_typo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_QUEUE_BACKEND, "redi")
    with pytest.raises(ConfigError) as exc_info:
        read_enum(
            MONET_QUEUE_BACKEND,
            ("memory", "redis", "sqlite", "upstash"),
            default="memory",
        )
    # Error message lists valid set so an operator can spot the typo.
    assert "redis" in str(exc_info.value)
    assert "memory" in str(exc_info.value)


def test_read_enum_accepts_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_QUEUE_BACKEND, "redis")
    assert (
        read_enum(
            MONET_QUEUE_BACKEND,
            ("memory", "redis", "sqlite", "upstash"),
            default="memory",
        )
        == "redis"
    )


# ── Patterned name helpers ─────────────────────────────────────────────


def test_graph_role_env_uppercases_role() -> None:
    assert graph_role_env("entry") == "MONET_GRAPH_ENTRY"
    assert graph_role_env("my-custom") == "MONET_GRAPH_MY-CUSTOM"


def test_pool_url_env_uppercases_pool_name() -> None:
    assert pool_url_env("gpu") == "MONET_POOL_GPU_URL"


def test_pool_auth_env_uppercases_pool_name() -> None:
    assert pool_auth_env("gpu") == "MONET_POOL_GPU_AUTH"


def test_agent_model_env_uppercases_agent_id() -> None:
    assert agent_model_env("planner") == "MONET_PLANNER_MODEL"


# ── TOML helpers ───────────────────────────────────────────────────────


def test_read_toml_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert read_toml(tmp_path / "nope.toml") == {}


def test_read_toml_parses_file(tmp_path: Path) -> None:
    toml = tmp_path / "monet.toml"
    toml.write_text("[graphs]\nentry = 'custom'\n", encoding="utf-8")
    raw = read_toml(toml)
    assert raw["graphs"]["entry"] == "custom"


# ── Entrypoints ────────────────────────────────────────────────────────


def test_default_entrypoints_include_run_and_chat() -> None:
    """Both CLIs (``monet run`` / ``monet chat``) get defaults in-code."""
    from monet.config import DEFAULT_ENTRYPOINTS

    assert "default" in DEFAULT_ENTRYPOINTS
    assert "chat" in DEFAULT_ENTRYPOINTS
    assert DEFAULT_ENTRYPOINTS["default"]["graph"] == "entry"
    assert DEFAULT_ENTRYPOINTS["chat"]["graph"] == "chat"


def test_user_can_override_chat_entrypoint(tmp_path: Path) -> None:
    """``[entrypoints.chat] graph = "..."`` in monet.toml wins over default."""
    from monet.config import load_entrypoints

    toml = tmp_path / "monet.toml"
    toml.write_text(
        '[entrypoints.chat]\ngraph = "my_chat"\n',
        encoding="utf-8",
    )
    eps = load_entrypoints(toml)
    assert eps["chat"]["graph"] == "my_chat"
    # Default remains alongside override.
    assert eps["default"]["graph"] == "entry"


def test_read_toml_section_empty_when_section_missing(
    tmp_path: Path,
) -> None:
    toml = tmp_path / "monet.toml"
    toml.write_text("[graphs]\nentry = 'x'\n", encoding="utf-8")
    assert read_toml_section("pools", toml) == {}


def test_default_config_path_respects_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    toml = tmp_path / "elsewhere.toml"
    toml.write_text("", encoding="utf-8")
    monkeypatch.setenv("MONET_CONFIG_PATH", str(toml))
    assert default_config_path() == toml


# ── Schemas: ObservabilityConfig ───────────────────────────────────────


def test_observability_load_is_empty_by_default() -> None:
    cfg = ObservabilityConfig.load()
    assert cfg.otlp_endpoint is None
    assert cfg.langfuse_public_key is None


def test_observability_redacted_summary_hides_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LANGFUSE_PUBLIC_KEY, "pk-lf-abc123")
    monkeypatch.setenv(LANGFUSE_SECRET_KEY, "sk-lf-xyz789")
    cfg = ObservabilityConfig.load()
    summary = cfg.redacted_summary()
    joined = " ".join(f"{k}={v}" for k, v in summary.items())
    assert "pk-lf-abc123" not in joined
    assert "sk-lf-xyz789" not in joined
    assert summary["langfuse"] == "set"


# ── Schemas: ArtifactsConfig ───────────────────────────────────────────


def test_artifacts_distributed_defaults_false() -> None:
    cfg = ArtifactsConfig.load()
    assert cfg.distributed is False


def test_artifacts_distributed_accepts_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_DISTRIBUTED, "yes")
    cfg = ArtifactsConfig.load()
    assert cfg.distributed is True


def test_artifacts_distributed_rejects_banana(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_DISTRIBUTED, "banana")
    with pytest.raises(ConfigError):
        ArtifactsConfig.load()


# ── Schemas: QueueConfig ───────────────────────────────────────────────


def test_queue_defaults_to_memory() -> None:
    cfg = QueueConfig.load()
    assert cfg.backend == "memory"


def test_queue_unknown_backend_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_QUEUE_BACKEND, "redi")
    with pytest.raises(ConfigError):
        QueueConfig.load()


def test_queue_redis_requires_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_QUEUE_BACKEND, "redis")
    cfg = QueueConfig.load()
    with pytest.raises(ConfigError) as exc_info:
        cfg.validate_for_boot()
    assert exc_info.value.var == REDIS_URI


def test_queue_memory_rejected_when_redis_uri_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guarantees a deployed server cannot silently fall back to in-memory.

    Replaces the ``MONET_ENV=production`` pattern: rather than a catch-all
    mode flag, the explicit signal is ``REDIS_URI`` being set while the
    backend is still ``memory`` — that combination is rejected at boot.
    """
    monkeypatch.setenv(REDIS_URI, "redis://localhost:6379")
    cfg = QueueConfig.load()
    with pytest.raises(ConfigError) as exc_info:
        cfg.validate_for_boot()
    assert exc_info.value.var == MONET_QUEUE_BACKEND


def test_queue_redis_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_QUEUE_BACKEND, "redis")
    monkeypatch.setenv(REDIS_URI, "redis://localhost:6379")
    QueueConfig.load().validate_for_boot()  # must not raise


def test_queue_redacted_summary_hides_redis_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REDIS_URI, "redis://user:supersecret@host:6379")
    cfg = QueueConfig.load()
    summary = cfg.redacted_summary()
    assert "supersecret" not in str(summary)
    assert summary["redis_uri"] == "set"


# ── Schemas: AuthConfig ────────────────────────────────────────────────


def test_auth_no_key_ok_when_not_required() -> None:
    AuthConfig.load().validate_for_boot(required=False)


def test_auth_no_key_raises_when_required() -> None:
    with pytest.raises(ConfigError) as exc_info:
        AuthConfig.load().validate_for_boot(required=True)
    assert exc_info.value.var == MONET_API_KEY


def test_auth_key_present_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_API_KEY, "secret")
    AuthConfig.load().validate_for_boot(required=True)


def test_auth_redacted_summary_hides_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_API_KEY, "secret-value-123")
    summary = AuthConfig.load().redacted_summary()
    assert "secret-value-123" not in str(summary)


# ── Schemas: OrchestrationConfig ───────────────────────────────────────


def test_orchestration_defaults_to_600s() -> None:
    cfg = OrchestrationConfig.load()
    assert cfg.agent_timeout == 600.0


def test_orchestration_rejects_malformed_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_AGENT_TIMEOUT, "abc")
    with pytest.raises(ConfigError):
        OrchestrationConfig.load()


def test_orchestration_rejects_zero_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_AGENT_TIMEOUT, "0")
    with pytest.raises(ConfigError):
        OrchestrationConfig.load()


# ── Schemas: ServerConfig ──────────────────────────────────────────────


def test_server_monolith_boots_without_api_key() -> None:
    """Monolith (non-distributed) server doesn't require an API key."""
    ServerConfig.load().validate_for_boot()


def test_server_distributed_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_DISTRIBUTED, "1")
    with pytest.raises(ConfigError) as exc_info:
        ServerConfig.load().validate_for_boot()
    assert exc_info.value.var == MONET_API_KEY


def test_server_distributed_with_key_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_DISTRIBUTED, "1")
    monkeypatch.setenv(MONET_API_KEY, "secret")
    ServerConfig.load().validate_for_boot()


def test_server_queue_backend_typo_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_QUEUE_BACKEND, "redi")
    with pytest.raises(ConfigError):
        ServerConfig.load()


def test_server_redacted_summary_is_nested_dict() -> None:
    summary = ServerConfig.load().redacted_summary()
    assert set(summary.keys()) == {
        "auth",
        "queue",
        "artifacts",
        "observability",
        "orchestration",
    }


# ── Schemas: WorkerConfig ──────────────────────────────────────────────


def test_worker_defaults() -> None:
    cfg = WorkerConfig.load()
    assert cfg.pool == "local"
    assert cfg.concurrency == 10


def test_worker_remote_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_SERVER_URL, "http://server.example")
    cfg = WorkerConfig.load()
    with pytest.raises(ConfigError) as exc_info:
        cfg.validate_for_boot()
    assert exc_info.value.var == MONET_API_KEY


def test_worker_remote_with_key_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MONET_SERVER_URL, "http://server.example")
    monkeypatch.setenv(MONET_API_KEY, "secret")
    WorkerConfig.load().validate_for_boot()


def test_worker_required_llm_keys_missing_raises() -> None:
    cfg = WorkerConfig.load().with_required_llm_keys((GEMINI_API_KEY, GROQ_API_KEY))
    with pytest.raises(ConfigError):
        cfg.validate_for_boot()


def test_worker_required_llm_key_one_of_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GROQ_API_KEY, "gsk-xxx")
    cfg = WorkerConfig.load().with_required_llm_keys((GEMINI_API_KEY, GROQ_API_KEY))
    cfg.validate_for_boot()  # at least one present → OK


# ── Schemas: ClientConfig ──────────────────────────────────────────────


def test_client_defaults_to_local_dev_url() -> None:
    cfg = ClientConfig.load()
    assert cfg.server_url.startswith("http://localhost:")


def test_client_picks_up_env_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MONET_SERVER_URL, "http://prod.example")
    assert ClientConfig.load().server_url == "http://prod.example"


# ── Schemas: CLIDevConfig ──────────────────────────────────────────────


def test_cli_dev_requires_llm_key() -> None:
    with pytest.raises(ConfigError):
        CLIDevConfig.load().validate_for_boot()


def test_cli_dev_with_gemini_key_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY, "g-xxx")
    CLIDevConfig.load().validate_for_boot()


def test_cli_dev_redacted_summary_hides_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY, "g-xxx-secret")
    monkeypatch.setenv(EXA_API_KEY, "exa-yyy-secret")
    summary = CLIDevConfig.load().redacted_summary()
    joined = str(summary)
    assert "g-xxx-secret" not in joined
    assert "exa-yyy-secret" not in joined


# ── Env isolation ──────────────────────────────────────────────────────


def test_env_isolation_part_1_sets_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Companion to part 2: set a MONET_* value here."""
    monkeypatch.setenv(MONET_API_KEY, "leaked-secret")
    assert AuthConfig.load().api_key == "leaked-secret"


def test_env_isolation_part_2_sees_unset() -> None:
    """The autouse fixture must have wiped MONET_API_KEY from part 1."""
    assert AuthConfig.load().api_key is None


def test_env_vars_registry_covers_critical_names() -> None:
    """MONET_ENV_VARS must enumerate every fixed MONET_* name so the
    test isolation fixture covers the full surface."""
    assert MONET_API_KEY in MONET_ENV_VARS
    assert MONET_QUEUE_BACKEND in MONET_ENV_VARS
    assert MONET_DISTRIBUTED in MONET_ENV_VARS
