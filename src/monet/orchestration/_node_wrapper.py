"""Node wrapper — the bridge between agents and the LangGraph graph.

Each node calls an agent, translates AgentResult to a lean state entry,
enforces content limits, reads signals, and triggers HITL interrupt
when needs_human_review is signaled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.types import interrupt

from monet._tracing import get_tracer
from monet.types import AgentResult, SignalType

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
    5. Calls interrupt() if needs_human_review signal present and
       interrupt_on_review is True
    6. Returns state update dict

    Args:
        agent_id: The agent's registered ID.
        command: Which command to invoke.
        content_limit: Max output chars before offload.
        interrupt_on_review: If True, call interrupt() when
            the agent signals needs_human_review. Default True.
    """

    async def node(state: GraphState) -> dict[str, Any]:
        tracer = get_tracer("monet.orchestration")
        with tracer.start_as_current_span(
            f"agent.{agent_id}.{command}",
            attributes={
                "agent.id": agent_id,
                "agent.command": command,
                "monet.run_id": state.get("run_id", ""),
            },
        ) as span:
            result: AgentResult = await invoke_agent(
                agent_id,
                command=command,
                task=state.get("task", ""),
                trace_id=state.get("trace_id", ""),
                run_id=state.get("run_id", ""),
            )

            # Translate signals to serializable dicts for lean state
            signals_data = [dict(s) for s in result.signals]

            entry: dict[str, Any] = {
                "agent_id": agent_id,
                "command": command,
                "output": result.output,
                "artifacts": [dict(a) for a in result.artifacts],
                "success": result.success,
                "signals": signals_data,
                "trace_id": result.trace_id,
                "run_id": result.run_id,
            }

            # Enforce content limit
            entry = await enforce_content_limit(entry, limit=content_limit)

            span.set_attribute("agent.success", result.success)

            # HITL: interrupt if agent signals needs_human_review
            needs_review = result.has_signal(SignalType.NEEDS_HUMAN_REVIEW)
            if interrupt_on_review and needs_review:
                review_signal = result.get_signal(SignalType.NEEDS_HUMAN_REVIEW)
                review_reason = (
                    review_signal.get("reason", "Agent requested human review")
                    if review_signal
                    else "Agent requested human review"
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
                "needs_review": needs_review,
            }

    node.__name__ = f"{agent_id}_{command}"
    node.__qualname__ = f"{agent_id}_{command}"
    return node
