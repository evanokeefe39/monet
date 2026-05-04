"""Tests for monet.server._config — pool topology configuration re-export.

The canonical pool config schema is now backend-based (not type-based).
Old type values (local/pull/push) are rejected at boot with migration guidance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from monet.server._config import load_config


def test_default_config_when_no_file(tmp_path: Path) -> None:
    """No monet.toml present -> returns a single in_process pool named local."""
    result = load_config(tmp_path / "nonexistent.toml")
    assert "local" in result
    assert result["local"].backend == "in_process"
    assert result["local"].name == "local"


def test_parse_valid_toml_new_schema(tmp_path: Path) -> None:
    """Parse a config with the new backend-based schema."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text(
        """\
[pools.local]
backend = "in_process"

[pools.workers]
backend = "subprocess"
workload = "task"
concurrency = 4
task_timeout_s = 300
"""
    )

    result = load_config(config_file)
    assert len(result) == 2
    assert result["local"].backend == "in_process"
    assert result["workers"].backend == "subprocess"
    assert result["workers"].concurrency == 4


def test_legacy_type_local_rejected(tmp_path: Path) -> None:
    """Old type='local' raises ValueError with migration guidance."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text("[pools.local]\ntype = 'local'\n")
    with pytest.raises(ValueError, match="legacy"):
        load_config(config_file)


def test_legacy_type_pull_rejected(tmp_path: Path) -> None:
    """Old type='pull' raises ValueError with migration guidance."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text("[pools.workers]\ntype = 'pull'\n")
    with pytest.raises(ValueError, match="Migrate"):
        load_config(config_file)


def test_legacy_type_push_rejected(tmp_path: Path) -> None:
    """Old type='push' raises ValueError with migration guidance."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text("[pools.cloud]\ntype = 'push'\n")
    with pytest.raises(ValueError, match="backend"):
        load_config(config_file)


def test_invalid_backend_rejected(tmp_path: Path) -> None:
    """Unrecognised backend value raises ValueError."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text("[pools.broken]\nbackend = 'invalid'\n")
    with pytest.raises(ValueError, match="invalid backend"):
        load_config(config_file)


def test_lease_ttl_defaults(tmp_path: Path) -> None:
    """Missing lease_ttl defaults to 300."""
    config_file = tmp_path / "monet.toml"
    config_file.write_text("[pools.worker]\nbackend = 'subprocess'\n")
    result = load_config(config_file)
    assert result["worker"].lease_ttl == 300
