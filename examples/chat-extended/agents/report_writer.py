"""``report_writer`` — a stub reporting capability."""

from __future__ import annotations

from monet import agent

report_writer = agent("report_writer")


@report_writer(command="draft", description="Draft a short report from a brief.")
async def draft(task: str) -> str:
    """Return a canned short report.

    Replace this body with an LLM call (or a structured-output
    pipeline) to get a usable report writer. This stub demonstrates
    dispatch + result rendering in ``monet chat``.
    """
    return (
        "# Draft report\n\n"
        f"**Brief:** {task}\n\n"
        "## Summary\n"
        "Replace this body with a real LLM call. The goal of this example\n"
        "is to show that a user-declared ``@agent`` capability is\n"
        "discoverable by ``monet chat`` and dispatchable via\n"
        "``/report_writer:draft <task>``.\n"
    )
