"""Tests for the monet CLI commands."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from monet.cli import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "worker" in result.output
    assert "server" in result.output


def test_worker_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["worker", "--help"])
    assert result.exit_code == 0
    assert "--pool" in result.output
    assert "--concurrency" in result.output


def test_server_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["server", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.output


def test_dev_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["dev", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.output
    assert "--verbose" in result.output


def test_agent_progress_has_reasons_field() -> None:
    from monet.client._events import AgentProgress

    event = AgentProgress(
        run_id="r1",
        agent_id="researcher",
        status="agent:failed",
        reasons="ImportError: no module named exa_py",
    )
    assert event.reasons == "ImportError: no module named exa_py"


def test_agent_progress_reasons_defaults_empty() -> None:
    from monet.client._events import AgentProgress

    event = AgentProgress(run_id="r1", agent_id="researcher", status="running")
    assert event.reasons == ""


def test_build_agent_progress_extracts_reasons() -> None:
    from monet.client import _build_agent_progress

    event = _build_agent_progress(
        "run-1",
        {
            "agent": "researcher",
            "status": "agent:failed",
            "reasons": "key missing",
            "signal_types": ["SEMANTIC_ERROR"],
        },
    )
    assert event is not None
    assert event.run_id == "run-1"
    assert event.agent_id == "researcher"
    assert event.status == "agent:failed"
    assert event.reasons == "key missing"


def test_build_agent_progress_missing_agent_returns_none() -> None:
    from monet.client import _build_agent_progress

    assert _build_agent_progress("run-1", {"status": "anything"}) is None
    assert _build_agent_progress("run-1", {"agent": "", "status": "x"}) is None


def test_build_agent_progress_missing_reasons_defaults_empty() -> None:
    from monet.client import _build_agent_progress

    event = _build_agent_progress("run-1", {"agent": "researcher", "status": "running"})
    assert event is not None
    assert event.reasons == ""


def test_render_agent_progress_shows_reasons(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from monet.cli._render import render_event
    from monet.client._events import AgentProgress

    render_event(
        AgentProgress(
            run_id="r1",
            agent_id="researcher",
            status="agent:failed",
            reasons="ImportError: no module named exa_py",
        )
    )
    captured = capsys.readouterr()
    assert "[researcher]" in captured.out
    assert "agent:failed" in captured.out
    assert "ImportError: no module named exa_py" in captured.out


def test_render_agent_progress_hides_empty_reasons(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from monet.cli._render import render_event
    from monet.client._events import AgentProgress

    render_event(AgentProgress(run_id="r1", agent_id="researcher", status="running"))
    captured = capsys.readouterr()
    # No trailing indented detail line when reasons is empty.
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1


class TestDevFilter:
    """Unit tests for the curated log filter in ``monet dev``."""

    def test_drop_blank_lines(self) -> None:
        from monet.cli._dev import _should_drop

        assert _should_drop("")
        assert _should_drop("   ")

    def test_drop_known_noise(self) -> None:
        from monet.cli._dev import _should_drop

        assert _should_drop(
            "2026-04-11T09:08:00.056075Z [info  ] 336 changes detected "
            "[watchfiles.main] api_variant=local_dev"
        )
        assert _should_drop(
            "2026-04-11T09:08:04.615757Z [info  ] Application started up in "
            "5.661s [langgraph_api.timing.timer]"
        )
        assert _should_drop(
            "2026-04-11T09:08:01.454214Z [info  ] Starting In-Memory runtime "
            "with langgraph-api=0.7.100 [langgraph_runtime_inmem.lifespan]"
        )
        assert _should_drop("INFO:langgraph_api.cli:")
        assert _should_drop("        Welcome to")
        assert _should_drop("- 🚀 API: http://127.0.0.1:2024")
        assert _should_drop("Starting LangGraph dev server on port 2024...")
        assert _should_drop("Starting Aegra dev server on port 2026...")

    def test_drop_banner_art(self) -> None:
        from monet.cli._dev import _should_drop

        assert _should_drop("╦  ┌─┐┌┐┌┌─┐╔═╗┬─┐┌─┐┌─┐┬ ┬")
        assert _should_drop("╣  ├─┤││││ ┬║ ╦├┬┘├─┤├─┘├─┤")
        assert _should_drop("╩═╝┴ ┴┘└┘└─┘╚═╝┴└─┴ ┴┴  ┴ ┴")

    def test_pass_through_unknown_lines(self) -> None:
        from monet.cli._dev import _should_drop

        # Unknown lines — e.g. tracebacks, user exceptions — must not be dropped.
        assert not _should_drop("Traceback (most recent call last):")
        assert not _should_drop('  File "/app/graph.py", line 42, in build_graph')
        assert not _should_drop("ValueError: unexpected None")
        assert not _should_drop("RuntimeError: port 2024 already in use")
