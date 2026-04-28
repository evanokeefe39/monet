"""Turn streaming + HITL interrupt coordination for the chat TUI.

A single user submission can span:

1. An initial chat-graph stream (user → graph → assistant).
2. One or more HITL interrupts, each resolved by the user's next
   prompt submission, followed by a resume stream.

``run_turn`` drives the outer loop; ``drain_stream`` renders each
stream's events; :class:`InterruptCoordinator` holds the pending-future
used to hand the next prompt submission to the interrupt parser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from monet.cli.chat._constants import MAX_INTERRUPT_ROUNDS
from monet.cli.chat._hitl._coordinator import (
    BusySetter,
    FocusPrompt,
    InterruptCoordinator,
    WidgetMounter,
    WidgetUnmounter,
    Writer,
)
from monet.cli.chat._view import format_agent_header, format_progress_line
from monet.client._events import AgentProgress

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from monet.client import MonetClient

__all__ = [
    "BusySetter",
    "FocusPrompt",
    "InterruptCoordinator",
    "WidgetMounter",
    "WidgetUnmounter",
    "Writer",
    "drain_stream",
    "empty_stream",
    "run_turn",
]


async def empty_stream() -> AsyncIterator[str]:
    """Async generator that yields nothing — used to skip the initial stream."""
    return
    yield  # type: ignore[misc]  # makes this an async generator


async def drain_stream(
    stream: Any,
    writer: Writer,
    *,
    source: str,
    client: MonetClient,
    thread_id: str,
) -> bool:
    """Render events from *stream* to the transcript.

    Returns True when something was shown — either a streamed chunk or
    an assistant-fallback read from thread history. The source tag
    (``"initial"`` / ``"resume"``) is logged but not rendered.
    """
    import logging

    log = logging.getLogger("monet.cli.chat")

    streamed = False
    _agent_labels: dict[str, str] = {}
    _agent_headers: set[str] = set()
    async for chunk in stream:
        if isinstance(chunk, AgentProgress):
            aid = chunk.agent_id
            if chunk.command and (
                aid not in _agent_labels or ":" not in _agent_labels[aid]
            ):
                _agent_labels[aid] = f"{aid}:{chunk.command}"
            elif aid not in _agent_labels:
                _agent_labels[aid] = aid
            label = _agent_labels[aid]
            if label not in _agent_headers:
                writer(format_agent_header(label))
                _agent_headers.add(label)
            if chunk.status in {"agent:failed", "agent:error"}:
                _agent_headers.discard(label)
            line = format_progress_line(chunk)
            if line is not None:
                writer(line)
            log.info(
                "%s progress agent=%s status=%s",
                source,
                chunk.agent_id,
                chunk.status,
            )
            continue
        writer(f"[assistant] {chunk}")
        log.info("%s chunk len=%d", source, len(str(chunk)))
        streamed = True
    if streamed:
        return True
    log.info("%s stream yielded nothing; state read fallback", source)
    try:
        history = await client.chat.get_chat_history(thread_id)
    except Exception as exc:
        writer(f"[error] state read failed: {exc}")
        log.exception("get_chat_history failed")
        return True
    for msg in reversed(history):
        content: str = ""
        is_assistant = False
        if isinstance(msg, dict):
            is_assistant = msg.get("role") == "assistant" or msg.get("type") == "ai"
            content = str(msg.get("content") or "").strip()
        elif hasattr(msg, "content"):
            from langchain_core.messages import AIMessage

            is_assistant = isinstance(msg, AIMessage)
            content = str(msg.content or "").strip()
        if is_assistant and content:
            writer(f"[assistant] {content}")
            return True
    return False


async def run_turn(
    *,
    client: MonetClient,
    thread_id: str,
    first_stream: Any,
    coordinator: InterruptCoordinator,
    writer: Writer,
    busy_setter: BusySetter,
    focus_prompt: FocusPrompt,
    get_interrupt: Callable[[str], Awaitable[dict[str, Any] | None]],
    resume: Callable[[str, dict[str, Any]], Any],
    mount_widgets: WidgetMounter | None = None,
    unmount_widgets: WidgetUnmounter | None = None,
) -> None:
    """Drive one user turn: stream, handle interrupts, loop until idle.

    ``get_interrupt`` and ``resume`` are thin client-method references
    passed in so this coroutine stays pure over the chat namespace — it
    does not reach into ``client.chat`` directly for those verbs.
    """
    import logging

    log = logging.getLogger("monet.cli.chat")

    had_output = await drain_stream(
        first_stream, writer, source="initial", client=client, thread_id=thread_id
    )
    for _round in range(MAX_INTERRUPT_ROUNDS):
        pending = await get_interrupt(thread_id)
        if not pending:
            if not had_output:
                writer("[error] run ended without output — check rate limits or config")
                log.warning("turn ended with no output and no interrupt")
            return
        had_output = True
        log.info("interrupt pending tag=%s", pending.get("tag"))
        form = pending.get("values") or {}
        if not isinstance(form, dict) or not form.get("fields"):
            writer("[info] graph paused but no form schema — aborting")
            log.warning("interrupt payload missing form schema: %r", form)
            return
        decision = await coordinator.collect(
            form,
            writer=writer,
            busy_setter=busy_setter,
            focus_prompt=focus_prompt,
            mount_widgets=mount_widgets,
            unmount_widgets=unmount_widgets,
        )
        if decision is None:
            writer("[info] interrupt abandoned — turn ended without resume")
            log.warning("interrupt coordinator returned None; no resume sent")
            return
        log.info("resume payload=%r", decision)
        stream = resume(thread_id, decision)
        had_output = await drain_stream(
            stream, writer, source="resume", client=client, thread_id=thread_id
        )
    else:
        writer(
            f"[error] too many interrupt rounds ({MAX_INTERRUPT_ROUNDS})"
            " — aborting turn"
        )
        log.error("run_turn hit MAX_INTERRUPT_ROUNDS=%d", MAX_INTERRUPT_ROUNDS)
