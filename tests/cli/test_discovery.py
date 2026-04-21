"""Tests for AST-based agent discovery."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from monet.cli._discovery import DiscoveredAgent, discover_agents


def test_discover_direct_form(tmp_path: Path) -> None:
    """Direct @agent(...) decoration is discovered."""
    src = tmp_path / "agents.py"
    src.write_text(
        dedent("""\
        from monet import agent

        @agent("myagent", command="fast", pool="local")
        async def my_handler(task, context):
            pass
        """),
        encoding="utf-8",
    )
    results = discover_agents(tmp_path)
    assert len(results) == 1
    assert results[0] == DiscoveredAgent(
        file=src,
        agent_id="myagent",
        command="fast",
        pool="local",
        function_name="my_handler",
    )


def test_discover_partial_form(tmp_path: Path) -> None:
    """Partial form ``x = agent("id"); @x(command=...)`` is discovered."""
    src = tmp_path / "agents.py"
    src.write_text(
        dedent("""\
        from monet import agent

        x = agent("myagent")

        @x(command="deep")
        async def my_deep_handler(task, context):
            pass
        """),
        encoding="utf-8",
    )
    results = discover_agents(tmp_path)
    assert len(results) == 1
    assert results[0].agent_id == "myagent"
    assert results[0].command == "deep"
    assert results[0].pool == "local"
    assert results[0].function_name == "my_deep_handler"


def test_discover_default_values(tmp_path: Path) -> None:
    """Command defaults to 'fast' and pool defaults to 'local'."""
    src = tmp_path / "agents.py"
    src.write_text(
        dedent("""\
        from monet import agent

        @agent("bare")
        async def bare_handler(task, context):
            pass
        """),
        encoding="utf-8",
    )
    results = discover_agents(tmp_path)
    assert len(results) == 1
    assert results[0].command == "fast"
    assert results[0].pool == "local"


def test_discover_keyword_agent_id(tmp_path: Path) -> None:
    """The agent_id=... keyword form is discovered."""
    src = tmp_path / "agents.py"
    src.write_text(
        dedent("""\
        from monet import agent

        @agent(agent_id="kw_agent", command="run")
        async def kw_handler(task, context):
            pass
        """),
        encoding="utf-8",
    )
    results = discover_agents(tmp_path)
    assert len(results) == 1
    assert results[0].agent_id == "kw_agent"
    assert results[0].command == "run"


def test_discover_skips_unparseable(tmp_path: Path) -> None:
    """Files with syntax errors are silently skipped."""
    bad = tmp_path / "broken.py"
    bad.write_text("def oops(:\n    pass\n", encoding="utf-8")

    good = tmp_path / "ok.py"
    good.write_text(
        dedent("""\
        from monet import agent

        @agent("good")
        async def handler(task, context):
            pass
        """),
        encoding="utf-8",
    )
    results = discover_agents(tmp_path)
    assert len(results) == 1
    assert results[0].agent_id == "good"


def test_discover_reference_agents() -> None:
    """discover_agents finds the 8 reference agents in src/monet/agents."""
    agents_dir = Path(__file__).resolve().parent.parent / "src" / "monet" / "agents"
    results = discover_agents(agents_dir)

    found = {(a.agent_id, a.command) for a in results}
    expected = {
        ("planner", "fast"),
        ("planner", "plan"),
        ("researcher", "fast"),
        ("researcher", "deep"),
        ("writer", "deep"),
        ("qa", "fast"),
        ("evaluator", "compare"),
        ("publisher", "publish"),
    }
    assert expected == found, f"Missing: {expected - found}, Extra: {found - expected}"


def test_discover_ignores_venv(tmp_path: Path) -> None:
    """.venv directories are skipped during scanning."""
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    hidden = venv_dir / "sneaky.py"
    hidden.write_text(
        dedent("""\
        from monet import agent

        @agent("hidden")
        async def hidden_handler(task, context):
            pass
        """),
        encoding="utf-8",
    )
    results = discover_agents(tmp_path)
    assert len(results) == 0


def test_discover_single_file(tmp_path: Path) -> None:
    """discover_agents works when given a single file path."""
    src = tmp_path / "solo.py"
    src.write_text(
        dedent("""\
        from monet import agent

        @agent("solo", command="run", pool="gpu")
        async def solo_handler(task, context):
            pass
        """),
        encoding="utf-8",
    )
    results = discover_agents(src)
    assert len(results) == 1
    assert results[0].agent_id == "solo"
    assert results[0].pool == "gpu"
