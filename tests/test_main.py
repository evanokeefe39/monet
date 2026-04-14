# ruff: noqa: E501
"""Smoke test for python -m monet.

Spawns the CLI in a subprocess with a stub agents module that monkeypatches
the reference agents to return canned content. This exercises the full
startup path including catalogue.initialise() without needing API keys.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path  # noqa: TC003


def test_python_m_monet_runs(tmp_path: Path) -> None:
    """python -m monet 'message' completes without error."""
    sitecustomize = tmp_path / "sitecustomize.py"
    sitecustomize.write_text(
        textwrap.dedent(
            """
            from unittest.mock import AsyncMock, patch
            from langchain_core.messages import AIMessage

            _patches = []

            def _mk(content):
                m = AsyncMock()
                m.ainvoke = AsyncMock(return_value=AIMessage(content=content))
                return m

            triage = '{"complexity": "complex", "suggested_agents": [], "requires_planning": true}'
            brief = ('{"goal": "g", "is_sensitive": false, "nodes": ['
                     '{"id": "draft", "depends_on": [],'
                     ' "agent_id": "writer", "command": "deep", "task": "x"}]}')

            import monet.agents.planner
            import monet.agents.writer
            import monet.agents.qa

            from itertools import cycle
            triage_msg = AIMessage(content=triage)
            brief_msg = AIMessage(content=brief)
            planner_responses = iter([triage_msg, brief_msg])
            planner_mock = AsyncMock()
            planner_mock.ainvoke = AsyncMock(side_effect=lambda *a, **kw: next(planner_responses))

            _patches.append(patch.object(monet.agents.planner, "_get_model", return_value=planner_mock))
            _patches.append(patch.object(monet.agents.writer, "_get_model", return_value=_mk("Some content")))
            _patches.append(patch.object(monet.agents.qa, "_get_model",
                return_value=_mk('{"verdict": "pass", "confidence": 0.9, "notes": "ok"}')))
            for p in _patches:
                p.start()
            """
        )
    )

    env = {
        "PYTHONPATH": str(tmp_path)
        + (
            (";" if sys.platform == "win32" else ":")
            + __import__("os").environ.get("PYTHONPATH", "")
        ),
        "MONET_CATALOGUE_DIR": str(tmp_path / ".catalogue"),
        "PATH": __import__("os").environ.get("PATH", ""),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
    }

    proc = subprocess.run(
        [sys.executable, "-m", "monet", "Test message"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "execution" in proc.stdout
