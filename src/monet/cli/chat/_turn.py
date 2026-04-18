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

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from monet.cli.chat._hitl import format_form_prompt, parse_text_reply
from monet.cli.chat._view import format_progress_line
from monet.client._events import AgentProgress

if TYPE_CHECKING:
    from monet.client import MonetClient

#: Transcript writer — any callable that takes a single pre-formatted line.
Writer = Callable[[str], None]

#: Busy-state toggle — drives the spinner + border pulse.
BusySetter = Callable[[bool], None]

#: Focus the prompt ``Input`` widget so the user knows where to type.
FocusPrompt = Callable[[], None]


class InterruptCoordinator:
    """Owns the ``Future`` used to hand a prompt submission to ``collect``.

    One coordinator per :class:`ChatApp`. ``consume_if_pending`` is
    called from ``on_input_submitted``; ``collect`` is called from
    within the turn loop when a HITL form is waiting.
    """

    def __init__(self) -> None:
        self._pending: asyncio.Future[str] | None = None

    def is_pending(self) -> bool:
        return self._pending is not None and not self._pending.done()

    def consume_if_pending(self, text: str) -> bool:
        """If a ``collect`` call is awaiting, deliver *text* to it.

        Returns True when the text was handed off (caller should not
        start a new chat turn), False otherwise.
        """
        pending = self._pending
        if pending is not None and not pending.done():
            self._pending = None
            pending.set_result(text)
            return True
        return False

    async def collect(
        self,
        form: dict[str, Any],
        *,
        writer: Writer,
        busy_setter: BusySetter,
        focus_prompt: FocusPrompt,
    ) -> dict[str, Any] | None:
        """Render *form* in the transcript and parse the next user reply.

        Loops on parse failure so a typo (``aprove``) becomes a re-prompt
        rather than a silent reject.
        """
        for line in format_form_prompt(form):
            writer(line)
        first = True
        while True:
            if not first:
                writer(
                    "[error] didn't recognise that — reply: "
                    "approve | revise <feedback> | reject"
                )
            first = False
            # Pause "busy" so the user can submit; spinner stays off
            # until the resume kicks the next stream. HITL waits read
            # as idle so the prompt border pulses to cue "reply here".
            busy_setter(False)
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            self._pending = future
            focus_prompt()
            try:
                text = await future
            finally:
                self._pending = None
            payload = parse_text_reply(form, text)
            if payload is not None:
                # Re-arm busy state for the resume stream.
                busy_setter(True)
                return payload


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
    async for chunk in stream:
        if isinstance(chunk, AgentProgress):
            # Progress events are intermediate signal — render them
            # but don't suppress the assistant-fallback below if no
            # actual reply lands.
            writer(format_progress_line(chunk))
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
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = str(msg.get("content") or "").strip()
            if content:
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
    while True:
        pending = await get_interrupt(thread_id)
        if not pending:
            if not had_output:
                writer("[info] (no assistant response)")
                log.warning("turn ended with no output and no interrupt")
            return
        had_output = True  # interrupt form counts as output
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
        )
        if decision is None:
            writer("[info] interrupt skipped — sending reject")
            decision = {"action": "reject", "feedback": ""}
        log.info("resume payload=%r", decision)
        stream = resume(thread_id, decision)
        had_output = await drain_stream(
            stream, writer, source="resume", client=client, thread_id=thread_id
        )
