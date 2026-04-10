"""Tests for monet.server._config — pool topology configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from monet.server._config import PoolConfig, load_config


def test_default_config_when_no_file(tmp_path: Path) -> None:
    """No monet.toml present -> returns a single local pool."""
    result = load_config(tmp_path / "nonexistent.toml")
    assert result == {"local": PoolConfig(name="local", type="local")}


def test_parse_valid_toml(tmp_path: Path) -> None:
    """Parse a complete config with all three pool types."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text(
        """\
[pools.local]
type = "local"

[pools.default]
type = "pull"
lease_ttl = 600

[pools.cloud]
type = "push"
url = "https://cloud.example.com/tasks"
"""
    )

    result = load_config(config_file)

    assert len(result) == 3
    assert result["local"] == PoolConfig(name="local", type="local")
    assert result["default"] == PoolConfig(name="default", type="pull", lease_ttl=600)
    assert result["cloud"] == PoolConfig(
        name="cloud",
        type="push",
        url="https://cloud.example.com/tasks",
    )


def test_env_var_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables populate url and auth fields."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text(
        """\
[pools.remote]
type = "pull"
"""
    )

    monkeypatch.setenv("MONET_POOL_REMOTE_URL", "https://remote.example.com")
    monkeypatch.setenv("MONET_POOL_REMOTE_AUTH", "secret-token")

    result = load_config(config_file)

    assert result["remote"].url == "https://remote.example.com"
    assert result["remote"].auth == "secret-token"


def test_push_pool_requires_url(tmp_path: Path) -> None:
    """Push pool without a URL (config or env) raises ValueError."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text(
        """\
[pools.cloud]
type = "push"
"""
    )

    with pytest.raises(ValueError, match="requires a URL"):
        load_config(config_file)


def test_invalid_pool_type(tmp_path: Path) -> None:
    """Bad pool type raises ValueError."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text(
        """\
[pools.broken]
type = "invalid"
"""
    )

    with pytest.raises(ValueError, match="Invalid pool type"):
        load_config(config_file)


def test_lease_ttl_defaults(tmp_path: Path) -> None:
    """Missing lease_ttl defaults to 300."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text(
        """\
[pools.worker]
type = "pull"
"""
    )

    result = load_config(config_file)

    assert result["worker"].lease_ttl == 300
