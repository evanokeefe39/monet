"""Phase functions over the LangGraph SDK client.

Three async helpers — one per graph — that the CLI orchestrates in
sequence:

  - :func:`run_triage`     entry graph: classify the request
  - :func:`run_planning`   planning graph: build a work brief, loop on
                           HITL approval until accepted or hard-rejected
  - :func:`run_execution`  execution graph: dispatch waves, loop on HITL
                           gate decisions until done or aborted

Interrupt detection follows the pattern surfaced by ``langgraph_sdk``:

  1. ``client.runs.stream(...)`` yields events until the run pauses or
     completes — there is no ``StopIteration`` distinguishing the two.
  2. After the stream iterator ends, fetch the latest thread state with
     ``client.threads.get_state(thread_id)``. ``state["next"]`` lists
     the nodes the runtime would execute next, which is empty when the
     run completed normally and contains the interrupt node name when
     paused.
  3. To resume, call ``client.runs.stream(thread_id, graph_id,
     command={"resume": payload})``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .client import stream_run
from .display import print_streaming_event

if TYPE_CHECKING:
    from collections.abc import Callable

    from langgraph_sdk.client import LangGraphClient


# Type alias for the HITL prompt callable. Tests inject fakes here.
DecisionPrompt = "Callable[[], dict[str, Any]]"


# ── Helpers ───────────────────────────────────────────────────────────


async def _drain_stream(
    client: LangGraphClient,
    thread_id: str,
    graph_id: str,
    label: str,
    *,
    input: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
) -> None:
    """Stream a run to completion, rendering events as they arrive.

    Either ``input`` or ``command`` must be supplied. Side effect only —
    callers fetch the resulting thread state separately.
    """
    async for mode, data in stream_run(
        client,
        thread_id,
        graph_id,
        input=input,
        command=command,
    ):
        if mode == "error":
            raise RuntimeError(f"server error during {label}: {data}")
        print_streaming_event(label, mode, data)


async def _get_state_values(
    client: LangGraphClient, thread_id: str
) -> tuple[dict[str, Any], list[str]]:
    """Return ``(values, next)`` for the current thread state."""
    state = await client.threads.get_state(thread_id)
    values = state.get("values") or {}
    nxt = list(state.get("next") or [])
    return values, nxt


# ── Phase 1: triage ───────────────────────────────────────────────────


async def run_triage(
    client: LangGraphClient,
    thread_id: str,
    topic: str,
    run_id: str,
) -> dict[str, Any]:
    """Run the entry graph and return the triage payload.

    The entry graph has no interrupts — one stream pass is enough.
    """
    await _drain_stream(
        client,
        thread_id,
        "entry",
        label="triage",
        input={
            "task": topic,
            "trace_id": f"trace-{run_id}",
            "run_id": run_id,
        },
    )
    values, _ = await _get_state_values(client, thread_id)
    triage = values.get("triage") or {}
    return dict(triage)


# ── Phase 2: planning + HITL approval loop ────────────────────────────


_MAX_PLANNING_ROUNDS = 5


async def run_planning(
    client: LangGraphClient,
    thread_id: str,
    topic: str,
    run_id: str,
    *,
    decision_prompt: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Run the planning graph with the HITL approval loop.

    ``decision_prompt`` is called with the current draft work brief
    every time the graph interrupts at ``human_approval``. It must
    return one of the dicts documented on
    :func:`prompts.prompt_planning_decision`.

    Returns the final state values dict — guaranteed to contain
    ``plan_approved`` and (if approved) ``work_brief``.
    """
    await _drain_stream(
        client,
        thread_id,
        "planning",
        label="planning",
        input={
            "task": topic,
            "trace_id": f"trace-{run_id}",
            "run_id": run_id,
            "revision_count": 0,
        },
    )

    for _round in range(_MAX_PLANNING_ROUNDS):
        values, nxt = await _get_state_values(client, thread_id)
        if values.get("plan_approved"):
            return values
        if "human_approval" not in nxt:
            return values

        brief = values.get("work_brief") or {}
        decision = decision_prompt(brief)

        await _drain_stream(
            client,
            thread_id,
            "planning",
            label="planning",
            command={"resume": decision},
        )

        # Hard reject — exit immediately, no replanning round.
        if decision.get("approved") is False and not decision.get("feedback"):
            values, _ = await _get_state_values(client, thread_id)
            return values

    return (await _get_state_values(client, thread_id))[0]


# ── Phase 3: execution + HITL gate loop ───────────────────────────────


async def run_execution(
    client: LangGraphClient,
    thread_id: str,
    work_brief: dict[str, Any],
    run_id: str,
    *,
    gate_prompt: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Run the execution graph with the HITL gate loop.

    ``gate_prompt`` is called with the latest QA reflection every time
    the graph interrupts at ``human_interrupt``. It must return one of
    the dicts documented on :func:`prompts.prompt_execution_decision`.

    Returns the final state values dict.
    """
    await _drain_stream(
        client,
        thread_id,
        "execution",
        label="execution",
        input={
            "work_brief": work_brief,
            "trace_id": f"trace-{run_id}",
            "run_id": run_id,
            "current_phase_index": 0,
            "current_wave_index": 0,
            "wave_results": [],
            "wave_reflections": [],
            "completed_phases": [],
            "revision_count": 0,
        },
    )

    while True:
        values, nxt = await _get_state_values(client, thread_id)
        if "human_interrupt" not in nxt:
            return values

        reflections = values.get("wave_reflections") or []
        last = reflections[-1] if reflections else {}
        decision = gate_prompt(last)

        await _drain_stream(
            client,
            thread_id,
            "execution",
            label="execution",
            command={"resume": decision},
        )

        if decision.get("action") == "abort":
            return (await _get_state_values(client, thread_id))[0]
