"""E2E-02 — manual HITL via form-schema interrupts.

Drives the compound default graph against a real server via
``MonetClient`` and exercises the three plan-decision options:
``approve``, ``revise``, ``reject``. After Track B's collapse, the
default pipeline is one graph on one thread — the client streams it
end-to-end, pauses on the planning subgraph's ``interrupt(...)``,
and resumes via ``client.resume(run_id, tag, {"action": ...})``.
"""

from __future__ import annotations

from typing import Any

import pytest

from monet.client import Interrupt, MonetClient, RunComplete, RunFailed
from monet.client._wire import task_input

TOPIC = "AI trends in healthcare"


async def _drive_until_interrupt(
    client: MonetClient,
    topic: str,
) -> tuple[str, Interrupt]:
    """Stream the default graph until an Interrupt event appears.

    Returns the (run_id, interrupt). Raises if the run completes or
    fails before any interrupt is seen.
    """
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
        raise AssertionError("pipeline ended before any Interrupt")
    return run_id, interrupt_event


@pytest.mark.e2e
async def test_approve_drives_execution_to_completion(
    monet_dev_server: str,
) -> None:
    client = MonetClient(monet_dev_server)
    run_id, ev = await _drive_until_interrupt(client, TOPIC)

    await client.resume(run_id, ev.tag, {"action": "approve"})

    saw_complete = False
    async for ev2 in client.run("default", None):
        if isinstance(ev2, RunComplete):
            saw_complete = True
            break
        if isinstance(ev2, RunFailed):
            pytest.fail(f"run failed during execution: {ev2.error}")
    assert saw_complete, "expected RunComplete after approve"


@pytest.mark.e2e
async def test_revise_then_approve(monet_dev_server: str) -> None:
    client = MonetClient(monet_dev_server)
    run_id, ev = await _drive_until_interrupt(client, TOPIC)

    await client.resume(
        run_id,
        ev.tag,
        {"action": "revise", "feedback": "make the plan shorter"},
    )

    # Planning subgraph re-emits an interrupt after revision.
    second: Interrupt | None = None
    async for ev2 in client.run("default", None):
        if isinstance(ev2, Interrupt):
            second = ev2
            break
        if isinstance(ev2, RunComplete | RunFailed):
            break
    assert second is not None, "expected a second interrupt after revise"

    await client.resume(run_id, second.tag, {"action": "approve"})

    saw_complete = False
    async for ev3 in client.run("default", None):
        if isinstance(ev3, RunComplete):
            saw_complete = True
            break
        if isinstance(ev3, RunFailed):
            pytest.fail(f"run failed: {ev3.error}")
    assert saw_complete, "expected RunComplete after revise → approve"


@pytest.mark.e2e
async def test_reject_halts_pipeline(monet_dev_server: str) -> None:
    client = MonetClient(monet_dev_server)
    run_id, ev = await _drive_until_interrupt(client, TOPIC)

    await client.resume(run_id, ev.tag, {"action": "reject"})

    # After rejection, the pipeline ends without execution.
    final_state: dict[str, Any] | None = None
    async for ev2 in client.run("default", None):
        if isinstance(ev2, RunComplete):
            final_state = dict(ev2.final_values)
            break
        if isinstance(ev2, RunFailed):
            final_state = {}
            break
    assert final_state is not None, "expected pipeline to terminate"
    # plan_approved should be False, no wave_results.
    assert not final_state.get("wave_results")
