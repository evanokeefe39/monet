"""Typed HITL verbs for the default pipeline.

Each verb builds a typed payload and delegates to
:meth:`MonetClient.resume` with the matching :data:`DefaultInterruptTag`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.client import MonetClient


async def approve_plan(client: MonetClient, run_id: str) -> None:
    """Approve a pending plan — resume planning into execution."""
    await client.resume(run_id, "human_approval", {"approved": True})


async def revise_plan(client: MonetClient, run_id: str, feedback: str) -> None:
    """Send a plan back for revision with ``feedback``."""
    await client.resume(
        run_id,
        "human_approval",
        {"approved": False, "feedback": feedback},
    )


async def reject_plan(client: MonetClient, run_id: str) -> None:
    """Reject a plan and terminate the run."""
    await client.resume(
        run_id,
        "human_approval",
        {"approved": False, "feedback": None},
    )


async def retry_wave(client: MonetClient, run_id: str) -> None:
    """Retry execution after an interrupt — no explicit action."""
    await client.resume(run_id, "human_interrupt", {"action": None})


async def abort_run(client: MonetClient, run_id: str) -> None:
    """Abort the run during an execution interrupt."""
    await client.resume(run_id, "human_interrupt", {"action": "abort"})


__all__ = [
    "abort_run",
    "approve_plan",
    "reject_plan",
    "retry_wave",
    "revise_plan",
]
