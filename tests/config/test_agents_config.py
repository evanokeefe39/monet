"""Tests for declarative agent config loading from agents.toml."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from monet.core._agent_loader import load_agents
from monet.core.registry import default_registry


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    """Isolate each test with a scratch registry restored on teardown."""
    with default_registry.registry_scope():
        default_registry.clear()
        yield


def _write_agents_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "agents.toml"
    p.write_text(content)
    return p


class TestLoadAgents:
    """Basic loading and registration."""

    def test_registers_http_agent(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "writer"
command = "deep"
pool = "gpu"
description = "Generate content"

[agent.transport]
type = "http"
url = "http://writer:8080/run"
""",
        )
        count = load_agents(path)
        assert count == 1
        assert default_registry.exists("writer", "deep")

    def test_registers_cli_agent(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "qa"
command = "fast"

[agent.transport]
type = "cli"
cmd = ["python", "qa.py"]
""",
        )
        count = load_agents(path)
        assert count == 1
        assert default_registry.exists("qa", "fast")

    def test_registers_sse_agent(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "monitor"
command = "fast"

[agent.transport]
type = "sse"
url = "http://monitor:9090/stream"
""",
        )
        count = load_agents(path)
        assert count == 1
        assert default_registry.exists("monitor", "fast")

    def test_registers_multiple_agents(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "a"
command = "fast"
[agent.transport]
type = "http"
url = "http://a:8080"

[[agent]]
id = "b"
command = "deep"
[agent.transport]
type = "cli"
cmd = ["python", "b.py"]
""",
        )
        count = load_agents(path)
        assert count == 2
        assert default_registry.exists("a", "fast")
        assert default_registry.exists("b", "deep")

    def test_defaults_command_to_fast(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "http"
url = "http://x:8080"
""",
        )
        load_agents(path)
        assert default_registry.exists("x", "fast")

    def test_defaults_pool_to_local(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "http"
url = "http://x:8080"
""",
        )
        load_agents(path)
        handler = default_registry.lookup("x", "fast")
        assert handler is not None
        assert handler._pool == "local"  # type: ignore[attr-defined]

    def test_empty_file_registers_nothing(self, tmp_path: Path) -> None:
        path = _write_agents_toml(tmp_path, "")
        count = load_agents(path)
        assert count == 0


class TestValidation:
    """Error handling for invalid config."""

    def test_missing_id(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
command = "fast"
[agent.transport]
type = "http"
url = "http://x:8080"
""",
        )
        with pytest.raises(ValueError, match="missing required 'id'"):
            load_agents(path)

    def test_missing_transport(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
""",
        )
        with pytest.raises(ValueError, match="missing 'transport'"):
            load_agents(path)

    def test_invalid_transport_type(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "grpc"
""",
        )
        with pytest.raises(ValueError, match="invalid transport type"):
            load_agents(path)

    def test_http_missing_url(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "http"
""",
        )
        with pytest.raises(ValueError, match="requires 'url'"):
            load_agents(path)

    def test_cli_missing_cmd(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "cli"
""",
        )
        with pytest.raises(ValueError, match="requires 'cmd'"):
            load_agents(path)

    def test_duplicate_agent_id_raises(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "dup"
command = "fast"
[agent.transport]
type = "http"
url = "http://a:8080"

[[agent]]
id = "dup"
command = "fast"
[agent.transport]
type = "http"
url = "http://b:8080"
""",
        )
        with pytest.raises(ValueError, match="conflicts with"):
            load_agents(path)

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_agents(tmp_path / "nonexistent.toml")

    def test_invalid_event_type(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "http"
url = "http://x:8080"

[[agent.on]]
event = "bogus"
type = "webhook"
url = "http://hook:9090"
""",
        )
        with pytest.raises(ValueError, match="invalid event"):
            load_agents(path)

    def test_invalid_handler_type(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "http"
url = "http://x:8080"

[[agent.on]]
event = "progress"
type = "grpc"
""",
        )
        with pytest.raises(ValueError, match="invalid handler type"):
            load_agents(path)

    def test_python_handler_bad_spec(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "http"
url = "http://x:8080"

[[agent.on]]
event = "progress"
type = "python"
handler = "no_colon_here"
""",
        )
        with pytest.raises(ValueError, match=r"module\.path:function_name"):
            load_agents(path)

    def test_python_handler_module_not_found(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "http"
url = "http://x:8080"

[[agent.on]]
event = "progress"
type = "python"
handler = "nonexistent_module_xyz:func"
""",
        )
        with pytest.raises(ValueError, match="could not import"):
            load_agents(path)

    def test_webhook_handler_missing_url(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "http"
url = "http://x:8080"

[[agent.on]]
event = "progress"
type = "webhook"
""",
        )
        with pytest.raises(ValueError, match="requires 'url'"):
            load_agents(path)

    def test_bash_handler_missing_cmd(self, tmp_path: Path) -> None:
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "x"
[agent.transport]
type = "http"
url = "http://x:8080"

[[agent.on]]
event = "signal"
type = "bash"
""",
        )
        with pytest.raises(ValueError, match="requires 'cmd'"):
            load_agents(path)


class TestEventHandlerRegistration:
    """Event handlers are registered and wired to the stream."""

    def test_webhook_handler_registered(self, tmp_path: Path) -> None:
        """Agent with webhook on-handler registers without error."""
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "hooked"
command = "fast"

[agent.transport]
type = "http"
url = "http://x:8080"

[[agent.on]]
event = "progress"
type = "webhook"
url = "http://hook:9090"
""",
        )
        count = load_agents(path)
        assert count == 1
        assert default_registry.exists("hooked", "fast")

    def test_python_handler_from_stdlib(self, tmp_path: Path) -> None:
        """Python handler referencing a stdlib callable resolves."""
        path = _write_agents_toml(
            tmp_path,
            """
[[agent]]
id = "pyhooked"
command = "fast"

[agent.transport]
type = "http"
url = "http://x:8080"

[[agent.on]]
event = "progress"
type = "python"
handler = "json:dumps"
""",
        )
        count = load_agents(path)
        assert count == 1
        assert default_registry.exists("pyhooked", "fast")
