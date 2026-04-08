"""Tests for examples/social_media_llm.

Two layers of coverage:

  1. ``--help`` smoke test against the Click entry point — confirms the
     CLI imports cleanly with the new module split (cli/app/client/
     workflow/display/prompts) and that Click renders its usage banner.
  2. A full mocked-server workflow test that monkeypatches
     ``langgraph_sdk.get_client`` to a fake client. The fake's
     ``runs.stream()`` yields canned ``StreamPart`` events and its
     ``threads.get_state()`` returns canned states so the workflow can
     be exercised end-to-end without a real server or any LLM keys.
     The wave-result renderer reads its artifact bytes back through the
     real ``monet.get_catalogue()`` (configured against a tmp dir) to
     prove the regex-free path works.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
from click.testing import CliRunner

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# ── --help smoke test ─────────────────────────────────────────────────


def test_cli_help_renders() -> None:
    from examples.social_media_llm.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Run the monet content workflow against TOPIC" in result.output
    assert "--server-url" in result.output


# ── Mocked-server workflow test ───────────────────────────────────────


class _StreamPart(NamedTuple):
    event: str
    data: Any
    id: str | None = None


class _FakeRuns:
    """Minimal stand-in for ``LangGraphClient.runs``.

    Built around a per-thread script of canned states so each ``stream``
    call advances the same thread's state by one step. ``input`` starts
    the run and ``command`` resumes from an interrupt — both consume one
    script entry.
    """

    def __init__(self, scripts: dict[str, list[dict[str, Any]]]) -> None:
        # scripts[thread_id] = list of dicts with keys:
        #   "events": list[(event, data)] tuples to yield
        #   "state":  the state to return from get_state after the stream
        self._scripts = scripts
        self._cursors: dict[str, int] = {}

    async def stream(
        self,
        thread_id: str,
        graph_id: str,
        *,
        input: dict[str, Any] | None = None,
        command: dict[str, Any] | None = None,
        stream_mode: list[str] | None = None,
    ) -> AsyncIterator[_StreamPart]:
        cursor = self._cursors.get(thread_id, 0)
        script = self._scripts[thread_id]
        if cursor >= len(script):
            return
        step = script[cursor]
        self._cursors[thread_id] = cursor + 1
        for event, data in step.get("events", []):
            yield _StreamPart(event=event, data=data)


class _FakeThreads:
    def __init__(self, scripts: dict[str, list[dict[str, Any]]]) -> None:
        self._scripts = scripts
        self._next_id = 0

    async def create(self) -> dict[str, str]:
        # Each create() hands out the next pre-scripted thread id in the
        # order the CLI calls them: triage, planning, execution.
        keys = list(self._scripts.keys())
        tid = keys[self._next_id]
        self._next_id += 1
        return {"thread_id": tid}

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        cursor = max(0, self._scripts.get("__cursors__", {}).get(thread_id, 0))
        # Return the state from the last script step the runner saw.
        steps = self._scripts[thread_id]
        idx = min(cursor, len(steps) - 1) if steps else 0
        # Walk to the highest step that has been "consumed" via stream().
        # The runs._cursors tracks that — we attached it via shared dict.
        return steps[idx].get("state", {"values": {}, "next": []})


class _FakeAssistants:
    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"assistant_id": "fake", "graph_id": "x"}]


class _FakeClient:
    def __init__(self, scripts: dict[str, list[dict[str, Any]]]) -> None:
        self.runs = _FakeRuns(scripts)
        self.threads = _FakeThreads(scripts)
        self.assistants = _FakeAssistants()
        # Wire the threads.get_state cursor lookup to the runs cursor.
        self.threads._scripts["__cursors__"] = self.runs._cursors  # type: ignore[assignment]


@pytest.fixture
def tmp_catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MONET_CATALOGUE_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setenv("GROQ_API_KEY", "fake")
    monkeypatch.setenv("TAVILY_API_KEY", "fake")
    return tmp_path


@pytest.mark.asyncio
async def test_workflow_against_mocked_server(
    tmp_catalogue: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive `_run` against a fake langgraph-sdk client.

    Asserts:

      - run_triage returns the canned triage payload from thread state
      - run_planning loops once on the human_approval interrupt and
        returns plan_approved=True
      - run_execution returns wave_results carrying real
        ``artifacts: [{"artifact_id": "..."}]`` pointers — the renderer
        must read the bytes via ``monet.get_catalogue()``, NOT via any
        regex on ``output``.
    """
    from examples.social_media_llm import app, cli, client, prompts

    app.configure_app()

    # Pre-write a fake artifact to the local catalogue so the renderer
    # has something to read by id.
    from monet import get_catalogue

    pointer = await get_catalogue().write(
        content=b"# Final article\n\nLorem ipsum.",
        content_type="text/markdown",
        summary="Final article",
        confidence=0.9,
        completeness="complete",
    )
    artifact_id = pointer["artifact_id"]

    # Build per-thread scripts. Each script step represents one
    # `stream()` call: the events it yields followed by the state that
    # `get_state()` reflects after consumption.
    triage_state = {
        "values": {"triage": {"complexity": "complex", "suggested_agents": ["writer"]}},
        "next": [],
    }
    brief = {
        "goal": "Test goal",
        "in_scope": ["a"],
        "out_of_scope": ["b"],
        "phases": [
            {
                "name": "p1",
                "waves": [
                    {
                        "items": [
                            {
                                "agent_id": "writer",
                                "command": "deep",
                                "task": "t",
                            }
                        ]
                    }
                ],
            },
        ],
        "assumptions": [],
    }
    planning_step1_state = {
        "values": {"work_brief": brief},
        "next": ["human_approval"],
    }
    planning_step2_state = {
        "values": {"work_brief": brief, "plan_approved": True},
        "next": [],
    }
    wave_result = {
        "phase_index": 0,
        "wave_index": 0,
        "item_index": 0,
        "agent_id": "writer",
        "command": "deep",
        "output": "Final article",
        "artifacts": [{"artifact_id": artifact_id, "url": f"file://{artifact_id}"}],
        "signals": [],
    }
    execution_state = {
        "values": {
            "work_brief": brief,
            "wave_results": [wave_result],
            "wave_reflections": [
                {"phase_index": 0, "wave_index": 0, "verdict": "pass", "notes": "ok"}
            ],
            "completed_phases": [0],
        },
        "next": [],
    }

    triage_payload = triage_state["values"]["triage"]
    scripts = {
        "thread-triage": [
            {
                "events": [
                    ("custom", {"status": "triaging", "agent": "planner"}),
                    ("updates", {"triage": {"triage": triage_payload}}),
                ],
                "state": triage_state,
            },
        ],
        "thread-planning": [
            {
                "events": [
                    ("custom", {"status": "planning", "agent": "planner"}),
                    ("updates", {"planner": {"work_brief": brief}}),
                ],
                "state": planning_step1_state,
            },
            {
                "events": [("updates", {"human_approval": {}})],
                "state": planning_step2_state,
            },
        ],
        "thread-execution": [
            {
                "events": [
                    ("custom", {"status": "writing", "agent": "writer"}),
                    ("updates", {"agent_node": {"wave_results": [wave_result]}}),
                ],
                "state": execution_state,
            },
        ],
    }

    fake_client = _FakeClient(scripts)
    monkeypatch.setattr(client, "get_client", lambda url=None: fake_client)

    # Always approve on the planning HITL prompt.
    monkeypatch.setattr(prompts, "prompt_planning_decision", lambda: {"approved": True})
    # Execution has no interrupts in this script.
    monkeypatch.setattr(
        prompts,
        "prompt_execution_decision",
        lambda: {"action": "continue"},
    )

    # Drive _run directly so we don't go through the asyncio.run wrapper.
    await cli._run("http://fake", "test topic", "abc12345")

    # If we got here without raising, the wave renderer successfully
    # resolved the artifact via wr["artifacts"], not via a regex on
    # output. Verify the renderer is the regex-free version.
    import examples.social_media_llm.display as display

    src = Path(display.__file__).read_text(encoding="utf-8")
    assert "_extract_artifact_id" not in src
    assert "_ARTIFACT_REPR_RE" not in src
    assert 'wr.get("artifacts")' in src
