"""Tests for PlanesConfig, ProgressBackend, ProgressConfig."""

from __future__ import annotations

import pytest

from monet.config._env import ConfigError
from monet.config._schema import PlanesConfig, ProgressBackend, ProgressConfig


def test_progress_backend_values() -> None:
    assert ProgressBackend.POSTGRES == "postgres"
    assert ProgressBackend.SQLITE == "sqlite"


def test_progress_backend_is_str() -> None:
    for member in ProgressBackend:
        assert isinstance(member, str)


def test_progress_config_load_returns_none_when_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MONET_PROGRESS_BACKEND", raising=False)
    assert ProgressConfig.load() is None


def test_progress_config_load_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONET_PROGRESS_BACKEND", "sqlite")
    monkeypatch.delenv("MONET_PROGRESS_DSN", raising=False)
    cfg = ProgressConfig.load()
    assert cfg is not None
    assert cfg.backend == ProgressBackend.SQLITE
    assert cfg.dsn is None


def test_progress_config_load_postgres_with_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_PROGRESS_BACKEND", "postgres")
    monkeypatch.setenv("MONET_PROGRESS_DSN", "postgresql://localhost/monet")
    cfg = ProgressConfig.load()
    assert cfg is not None
    assert cfg.backend == ProgressBackend.POSTGRES
    assert cfg.dsn == "postgresql://localhost/monet"


def test_progress_config_invalid_backend_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_PROGRESS_BACKEND", "kafka")
    with pytest.raises(ConfigError):
        ProgressConfig.load()


def test_progress_config_validate_postgres_no_dsn() -> None:
    cfg = ProgressConfig(backend=ProgressBackend.POSTGRES, dsn=None)
    with pytest.raises(ConfigError):
        cfg.validate_for_boot()


def test_progress_config_validate_sqlite_no_dsn() -> None:
    cfg = ProgressConfig(backend=ProgressBackend.SQLITE, dsn=None)
    cfg.validate_for_boot()  # should not raise


def test_planes_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONET_DATA_PLANE_URL", raising=False)
    monkeypatch.delenv("MONET_PROGRESS_BACKEND", raising=False)
    cfg = PlanesConfig.load()
    assert cfg.data_url is None
    assert cfg.progress is None


def test_planes_config_data_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONET_DATA_PLANE_URL", "https://data.example.com")
    monkeypatch.delenv("MONET_PROGRESS_BACKEND", raising=False)
    cfg = PlanesConfig.load()
    assert cfg.data_url == "https://data.example.com"


def test_planes_config_redacted_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONET_PROGRESS_BACKEND", "sqlite")
    monkeypatch.delenv("MONET_DATA_PLANE_URL", raising=False)
    cfg = PlanesConfig.load()
    summary = cfg.redacted_summary()
    assert "progress_backend" in summary
    assert summary["progress_backend"] == "sqlite"
