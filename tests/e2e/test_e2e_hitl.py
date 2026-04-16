"""E2E-02 — manual HITL via form-schema interrupts.

Drives the compound default graph against a real server via
``MonetClient``. The planning subgraph's interrupt pauses the parent
thread; ``client.resume`` dispatches a form-schema payload and
internally streams the post-resume segment to completion. Final state
is observed via ``client.get_run``.

Picks a topic that the stock planner classifies as ``complex`` so the
entry subgraph doesn't short-circuit past planning.
"""

from __future__ import annotations

import pytest

from monet.client import Interrupt, MonetClient, RunComplete, RunFailed
from monet.client._wire import task_input

# Remaining open issue preventing reliable pass: triage nondeterminism
# — the stock planner/fast prompt sometimes classifies an explicitly
# multi-step topic as ``simple``, short-circuiting past planning so no
# Interrupt is emitted. Tracked as I7.
#
# The former resume/stream race (I6) is resolved —
# ``MonetClient._await_interrupted_status`` polls the thread row until
# Aegra commits ``status="interrupted"`` before dispatching resume, so
# the prior 400 race is now a deterministic no-op when hit.
#
# Track B's graph-side correctness is already validated by
# ``tests/test_default_compound_graph.py`` and the auto-approve E2E-01;
# leaving these HITL scenarios as ``xfail`` until the triage prompt
# tweak (I7) lands.
pytestmark = pytest.mark.xfail(
    reason=(
        "e2e HITL harness: triage nondeterminism (I7). "
        "Unit-level coverage in test_default_compound_graph.py."
    ),
    strict=False,
)

# Stock triage classifies narrow topics as "simple" and short-circuits
# the pipeline. An explicit multi-step research/writing request forces
# "complex", which routes through planning (→ interrupt) and execution.
TOPIC = (
    "Produce a comparative analysis of leading open-source LLM agent "
    "orchestration frameworks, including a strengths/weaknesses matrix "
    "and a recommendation for production use."
)


async def _drive_until_interrupt(
    client: MonetClient,
    topic: str,
) -> tuple[str, Interrupt]:
    """Stream the default graph until an Interrupt event appears."""
    run_id: str | None = None
    interrupt_event: Interrupt | None = None
    async for ev in client.run("default", task_input(topic, "")):
        if run_id is None and hasattr(ev, "run_id"):
            run_id = ev.run_id
        if isinstance(ev, Interrupt):
            interrupt_event = ev
            break
        if isinstance(ev, RunComplete | RunFailed):
            break
    if run_id is None:
        raise AssertionError("never observed a run_id")
    if interrupt_event is None:
        raise AssertionError(
            "pipeline ended before any Interrupt — triage may have "
            "classified the topic as 'simple'; pick a more complex topic."
        )
    return run_id, interrupt_event


@pytest.mark.e2e
async def test_approve_drives_execution_to_completion(
    monet_dev_server: str,
) -> None:
    client = MonetClient(monet_dev_server)
    run_id, ev = await _drive_until_interrupt(client, TOPIC)

    # client.resume() streams the post-resume segment to completion
    # internally; after it returns, the run should be done (or at the
    # next interrupt). Observe the final state via get_run.
    await client.resume(run_id, ev.tag, {"action": "approve"})

    detail = await client.get_run(run_id)
    assert detail.status != "interrupted", (
        f"expected run complete after approve; status={detail.status}"
    )
    # Execution subgraph should have produced wave_results when it ran.
    assert detail.values.get("plan_approved") is True


@pytest.mark.e2e
async def test_revise_then_approve(monet_dev_server: str) -> None:
    client = MonetClient(monet_dev_server)
    run_id, ev = await _drive_until_interrupt(client, TOPIC)

    # Revise with feedback → planner re-runs, then pauses again for
    # approval. client.resume streams to the next interrupt.
    await client.resume(
        run_id,
        ev.tag,
        {"action": "revise", "feedback": "keep it under 5 sections"},
    )

    detail = await client.get_run(run_id)
    assert detail.status == "interrupted", (
        f"expected second interrupt after revise; status={detail.status}"
    )
    assert detail.pending_interrupt is not None
    second_tag = detail.pending_interrupt.tag

    await client.resume(run_id, second_tag, {"action": "approve"})

    detail2 = await client.get_run(run_id)
    assert detail2.status != "interrupted", (
        f"expected completion after revise→approve; status={detail2.status}"
    )
    assert detail2.values.get("plan_approved") is True


@pytest.mark.e2e
async def test_reject_halts_pipeline(monet_dev_server: str) -> None:
    client = MonetClient(monet_dev_server)
    run_id, ev = await _drive_until_interrupt(client, TOPIC)

    await client.resume(run_id, ev.tag, {"action": "reject"})

    detail = await client.get_run(run_id)
    assert detail.status != "interrupted", (
        f"expected terminal status after reject; status={detail.status}"
    )
    # plan_approved should be False; no execution ran, so no wave_results.
    assert detail.values.get("plan_approved") is False
    assert not detail.values.get("wave_results")
