"""Node wrapper — the bridge between agents and the LangGraph graph.

Each node calls an agent, translates AgentResult to a lean state entry,
enforces content limits, reads signals, and triggers HITL interrupt
when needs_human_review is True.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from langgraph.types import interrupt

from monet._tracing import end_span, start_agent_span
from monet._types import AgentResult, AgentRunContext

from ._content_limit import enforce_content_limit
from ._invoke import invoke_agent

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from ._state import GraphState


def create_node(
    agent_id: str,
    command: str = "fast",
    content_limit: int = 4000,
    *,
    interrupt_on_review: bool = True,
) -> Callable[[GraphState], Coroutine[Any, Any, dict[str, Any]]]:
    """Create a LangGraph node function for an agent.

    The returned async function:
    1. Starts an OTel span
    2. Calls the agent via invoke_agent() (local or HTTP based on config)
    3. Translates AgentResult to a lean state entry
    4. Enforces content limit
    5. Calls interrupt() if needs_human_review and interrupt_on_review is True
    6. Returns state update dict

    Args:
        agent_id: The agent's registered ID.
        command: Which command to invoke.
        content_limit: Max output chars before offload.
        interrupt_on_review: If True, call interrupt() when
            the agent signals needs_human_review. Default True.
    """

    async def node(state: GraphState) -> dict[str, Any]:
        span = start_agent_span(
            agent_id=agent_id,
            command=command,
            run_id=state.get("run_id", ""),
            trace_id=state.get("trace_id", ""),
        )

        try:
            ctx = AgentRunContext(
                task=state.get("task", ""),
                command=command,
                trace_id=state.get("trace_id", ""),
                run_id=state.get("run_id", ""),
                agent_id=agent_id,
            )

            result: AgentResult = await invoke_agent(agent_id, command, ctx)

            # Translate to lean state entry
            error_dict = None
            if result.signals.semantic_error is not None:
                error_dict = dataclasses.asdict(result.signals.semantic_error)

            entry: dict[str, Any] = {
                "agent_id": agent_id,
                "command": command,
                "output": result.output
                if isinstance(result.output, str)
                else result.output.url,
                "success": result.success,
                "confidence": result.confidence,
                "needs_human_review": (result.signals.needs_human_review),
                "escalation_requested": (result.signals.escalation_requested),
                "semantic_error": error_dict,
                "trace_id": result.trace_id,
                "run_id": result.run_id,
            }

            # Enforce content limit
            entry = enforce_content_limit(entry, limit=content_limit)

            end_span(span, success=result.success)

            # HITL: interrupt if agent requests human review
            if interrupt_on_review and result.signals.needs_human_review:
                review_reason = (
                    result.signals.review_reason or "Agent requested human review"
                )
                interrupt(
                    {
                        "agent_id": agent_id,
                        "reason": review_reason,
                        "entry": entry,
                    }
                )

            return {
                "results": [entry],
                "needs_review": result.signals.needs_human_review,
            }

        except Exception as exc:
            end_span(span, success=False, error_message=str(exc))
            raise

    node.__name__ = f"{agent_id}_{command}"
    node.__qualname__ = f"{agent_id}_{command}"
    return node
