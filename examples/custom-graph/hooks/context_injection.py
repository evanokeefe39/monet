"""Worker hook — inject tone guidelines before specific agents."""

from pathlib import Path

from monet import AgentMeta, AgentRunContext, on_hook


@on_hook("before_agent", match="summarizer")
async def inject_tone(ctx: AgentRunContext, meta: AgentMeta) -> AgentRunContext:
    """Prepend a system-level tone instruction to the agent's context."""
    tone_file = Path(__file__).parent.parent / "config" / "tone.md"
    if tone_file.exists():
        tone = tone_file.read_text()
    else:
        tone = "Be concise and professional."

    return AgentRunContext(
        task=ctx["task"],
        context=[{"role": "system", "content": tone}, *ctx["context"]],
        command=ctx["command"],
        trace_id=ctx["trace_id"],
        run_id=ctx["run_id"],
        agent_id=ctx["agent_id"],
        skills=ctx["skills"],
    )
