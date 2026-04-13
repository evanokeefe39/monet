"""Worker hook — validate that agents produce non-trivial output."""

from monet import AgentMeta, AgentResult, SemanticError, on_hook


@on_hook("after_agent", match="*")
async def validate_output(result: AgentResult, meta: AgentMeta) -> AgentResult | None:
    """Reject results with empty output and no artifacts.

    This is a stricter version of the built-in poka-yoke guard.
    It raises SemanticError so the result is marked as failed.
    """
    if result.success and not result.output and not result.artifacts:
        raise SemanticError(
            type="empty_result",
            message=f"Agent {meta['agent_id']}/{meta['command']} produced nothing",
        )
    return None  # pass through unmodified
