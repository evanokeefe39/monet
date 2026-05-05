from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from monet.adapter._config import load_config


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "adapter.toml"
    p.write_text(textwrap.dedent(content))
    return p


def test_minimal_openai(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
        name = "hermes"
        type = "openai"
        url = "http://localhost:8642"
    """,
    )
    cfg = load_config(p)
    assert cfg.name == "hermes"
    assert cfg.type == "openai"
    assert cfg.port == 8080
    assert cfg.timeout == 300


def test_defaults_applied(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
        name = "x"
        type = "openai"
        url = "http://localhost:1234"
    """,
    )
    cfg = load_config(p)
    assert cfg.ready_timeout == 120
    assert cfg.model is None
    assert cfg.auth is None


def test_full_http(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
        name = "pi"
        type = "http"
        url = "http://localhost:9000/chat"
        health = "/health"

        [request]
        body.message = "$.payload.task"
        params = { stream = "false" }

        [response]
        output = "$.message"

        [process]
        command = ["npx", "tsx", "server.ts"]
        workdir = "/pi"
    """,
    )
    cfg = load_config(p)
    assert cfg.response.output == "$.message"
    assert cfg.request.body == {"message": "$.payload.task"}
    assert cfg.process.command == ["npx", "tsx", "server.ts"]


def test_top_level_command_merged(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
        name = "h"
        type = "openai"
        url = "http://localhost:8642"
        command = ["hermes-server"]
    """,
    )
    cfg = load_config(p)
    assert cfg.process.command == ["hermes-server"]


def test_openai_missing_url_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
        name = "x"
        type = "openai"
    """,
    )
    with pytest.raises(Exception, match="url"):
        load_config(p)


def test_http_missing_response_output_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
        name = "x"
        type = "http"
        url = "http://localhost:9000"
    """,
    )
    with pytest.raises(Exception, match="output"):
        load_config(p)


def test_stdio_missing_plugin_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """\
        name = "x"
        type = "stdio"

        [stdio]
        command = ["agent-bin"]
    """,
    )
    with pytest.raises(Exception, match="plugin"):
        load_config(p)


def test_env_interpolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_PORT", "9999")
    p = _write(
        tmp_path,
        """\
        name = "x"
        type = "openai"
        url = "http://localhost:${TEST_PORT}"
    """,
    )
    cfg = load_config(p)
    assert cfg.url == "http://localhost:9999"
