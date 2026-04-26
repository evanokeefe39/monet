"""Tests for ChatConfig: load + validate_for_boot + redacted_summary."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from monet.config import (
    MONET_CHAT_GRAPH,
    MONET_CHAT_RESPOND_MODEL,
    MONET_CHAT_TRIAGE_MODEL,
    MONET_CONFIG_PATH,
    ChatConfig,
    ConfigError,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each test in a clean env + a throwaway cwd so monet.toml doesn't leak."""
    for name in (
        MONET_CHAT_GRAPH,
        MONET_CHAT_RESPOND_MODEL,
        MONET_CHAT_TRIAGE_MODEL,
        MONET_CONFIG_PATH,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def test_defaults() -> None:
    cfg = ChatConfig.load()
    assert cfg.graph == "monet.orchestration.prebuilt.chat_graph:build_chat_graph"
    assert cfg.respond_model == "groq:llama-3.3-70b-versatile"
    assert cfg.triage_model == "groq:llama-3.3-70b-versatile"


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    override_graph = "monet.orchestration.prebuilt.chat_graph:build_chat_graph"
    monkeypatch.setenv(MONET_CHAT_GRAPH, override_graph)
    monkeypatch.setenv(MONET_CHAT_RESPOND_MODEL, "openai:gpt-4o-mini")
    monkeypatch.setenv(MONET_CHAT_TRIAGE_MODEL, "groq:llama-3.3-70b-versatile")
    cfg = ChatConfig.load()
    assert cfg.respond_model == "openai:gpt-4o-mini"
    assert cfg.triage_model == "groq:llama-3.3-70b-versatile"


def test_toml_section_picks_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml = tmp_path / "monet.toml"
    toml.write_text(
        "[chat]\n"
        'graph = "monet.orchestration.prebuilt.chat_graph:build_chat_graph"\n'
        'respond_model = "openai:gpt-4o"\n'
        'triage_model = "openai:gpt-4o-mini"\n'
    )
    monkeypatch.setenv(MONET_CONFIG_PATH, str(toml))
    cfg = ChatConfig.load()
    assert cfg.respond_model == "openai:gpt-4o"
    assert cfg.triage_model == "openai:gpt-4o-mini"


def test_env_beats_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml = tmp_path / "monet.toml"
    toml.write_text('[chat]\nrespond_model = "toml-value"\n')
    monkeypatch.setenv(MONET_CONFIG_PATH, str(toml))
    monkeypatch.setenv(MONET_CHAT_RESPOND_MODEL, "env-value")
    cfg = ChatConfig.load()
    assert cfg.respond_model == "env-value"


def test_validate_for_boot_default_path_resolves() -> None:
    ChatConfig.load().validate_for_boot()


def test_validate_for_boot_rejects_non_dotted_path() -> None:
    cfg = ChatConfig(graph="not-a-colon-path")
    with pytest.raises(ConfigError):
        cfg.validate_for_boot()


def test_validate_for_boot_rejects_missing_module() -> None:
    cfg = ChatConfig(graph="monet.definitely_not_a_real_module:factory")
    with pytest.raises(ConfigError):
        cfg.validate_for_boot()


def test_validate_for_boot_rejects_missing_attribute() -> None:
    cfg = ChatConfig(
        graph="monet.orchestration.prebuilt.chat_graph:nonexistent_factory"
    )
    with pytest.raises(ConfigError):
        cfg.validate_for_boot()


def test_redacted_summary_shape() -> None:
    cfg = ChatConfig.load()
    summary = cfg.redacted_summary()
    assert summary == {
        "graph": cfg.graph,
        "respond_model": cfg.respond_model,
        "triage_model": cfg.triage_model,
    }
