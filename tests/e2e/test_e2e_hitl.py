"""E2E-02 — manual HITL variants.

Drives the default pipeline against a real server via ``MonetClient``,
exercising the three plan-decision verbs:

- ``approve`` — pipeline runs to ``RunComplete`` after
  ``continue_after_plan_approval`` drives execution.
- ``revise`` — planning re-runs with feedback, then approves.
- ``reject`` — pipeline stops; no execution.

This test directly validates the fix for the ``_resume_pipeline`` stub.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from monet.client import Interrupt, MonetClient, RunComplete, RunFailed
from monet.pipelines.default import (
    PlanInterrupt,
    approve_plan,
    continue_after_plan_approval,
    reject_plan,
    revise_plan,
)
from monet.pipelines.default import (
    run as run_default,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _drive_until_plan_interrupt(
    client: MonetClient,
    topic: str,
) -> tuple[str | None, AsyncIterator[object]]:
    """Iterate run_default until a PlanInterrupt appears. Return run_id + gen."""
    run_id: str | None = None
    gen = run_default(client, topic, auto_approve=False)
    async for ev in gen:
        if run_id is None and hasattr(ev, "run_id"):
            run_id = ev.run_id
        if isinstance(ev, PlanInterrupt):
            return run_id, gen
        if isinstance(ev, RunComplete | RunFailed | Interrupt):
            break
    msg = "pipeline ended before PlanInterrupt — cannot exercise HITL"
    raise AssertionError(msg)


@pytest.mark.e2e
async def test_approve_drives_execution_to_completion(
    monet_dev_server: str,
) -> None:
    client = MonetClient(monet_dev_server)
    run_id, _ = await _drive_until_plan_interrupt(client, "AI trends in healthcare")
    assert run_id is not None

    await approve_plan(client, run_id)

    saw_complete = False
    async for ev in continue_after_plan_approval(client, run_id):
        if isinstance(ev, RunComplete):
            saw_complete = True
            break
        if isinstance(ev, RunFailed):
            pytest.fail(f"run failed during execution: {ev.error}")
    assert saw_complete, "expected RunComplete after approve"


@pytest.mark.e2e
async def test_revise_then_approve(monet_dev_server: str) -> None:
    client = MonetClient(monet_dev_server)
    run_id, _ = await _drive_until_plan_interrupt(client, "AI trends in healthcare")
    assert run_id is not None

    # First pass: ask for revision.
    await revise_plan(client, run_id, "make the plan shorter")

    # The planning thread loops and re-emits an interrupt. Re-drive.
    # NOTE: revise_plan dispatches the resume but doesn't wait for the
    # next interrupt — the thread is paused again awaiting human
    # approval. Approve and drive execution.
    await approve_plan(client, run_id)

    saw_complete = False
    async for ev in continue_after_plan_approval(client, run_id):
        if isinstance(ev, RunComplete):
            saw_complete = True
            break
        if isinstance(ev, RunFailed):
            pytest.fail(f"run failed: {ev.error}")
    assert saw_complete, "expected RunComplete after revise → approve"


@pytest.mark.e2e
async def test_reject_halts_pipeline(monet_dev_server: str) -> None:
    client = MonetClient(monet_dev_server)
    run_id, _ = await _drive_until_plan_interrupt(client, "AI trends in healthcare")
    assert run_id is not None

    await reject_plan(client, run_id)

    # continue_after_plan_approval should yield RunFailed because the
    # plan never got approved.
    saw_failed = False
    async for ev in continue_after_plan_approval(client, run_id):
        if isinstance(ev, RunFailed):
            saw_failed = True
            break
        if isinstance(ev, RunComplete):
            pytest.fail("RunComplete after reject — pipeline should halt")
    assert saw_failed, "expected RunFailed after reject"
