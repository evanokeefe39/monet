"""monet run — run a topic against a monet server with interactive HITL."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from monet.client import MonetClient

from monet.cli._render import (
    prompt_execution_decision,
    prompt_plan_decision,
    render_event,
)
from monet.cli._setup import check_env


@click.command()
@click.argument("topic")
@click.option(
    "--url",
    default="http://localhost:2024",
    envvar="MONET_SERVER_URL",
    help="LangGraph server URL.",
)
@click.option(
    "--auto-approve",
    is_flag=True,
    default=False,
    help="Auto-approve plans without prompting.",
)
def run(topic: str, url: str, auto_approve: bool) -> None:
    """Run a topic through the monet orchestration pipeline.

    Connects to a running monet LangGraph server, streams events to the
    terminal, and prompts for human decisions at plan approval and
    execution interrupt points.

    Use --auto-approve to skip plan approval prompts.
    """
    check_env()
    asyncio.run(_interactive_run(topic, url, auto_approve))


async def _interactive_run(topic: str, url: str, auto_approve: bool) -> None:
    """Run a topic with interactive HITL handling."""
    from monet.client import MonetClient
    from monet.client._events import (
        ExecutionInterrupt,
        PlanInterrupt,
        RunFailed,
    )

    client = MonetClient(url)
    run_id: str | None = None

    async for event in client.run(topic, auto_approve=auto_approve):
        render_event(event)

        # Capture run_id from first event.
        if run_id is None and hasattr(event, "run_id"):
            run_id = event.run_id

        # HITL: plan approval.
        if isinstance(event, PlanInterrupt) and run_id:
            decision = prompt_plan_decision()

            if decision == "approve":
                async for follow_up in client.approve_plan(run_id):
                    render_event(follow_up)
                    if isinstance(follow_up, ExecutionInterrupt):
                        await _handle_execution_interrupt(client, run_id)

            elif decision == "revise":
                feedback = click.prompt("Feedback")
                async for follow_up in client.revise_plan(run_id, feedback):
                    render_event(follow_up)

            elif decision == "reject":
                await client.reject_plan(run_id)
                click.secho("Run rejected.", fg="red")

            return  # Stream ended at interrupt; HITL methods handle the rest.

        # HITL: execution interrupt.
        if isinstance(event, ExecutionInterrupt) and run_id:
            await _handle_execution_interrupt(client, run_id)
            return

        if isinstance(event, RunFailed):
            raise SystemExit(1)


async def _handle_execution_interrupt(
    client: MonetClient,
    run_id: str,
) -> None:
    """Prompt for retry/abort on an execution interrupt."""
    from monet.client._events import RunFailed

    decision = prompt_execution_decision()

    if decision == "retry":
        async for event in client.retry_wave(run_id):
            render_event(event)
            if isinstance(event, RunFailed):
                raise SystemExit(1)
    else:
        await client.abort_run(run_id)
        click.secho("Run aborted.", fg="red")
        raise SystemExit(1)
