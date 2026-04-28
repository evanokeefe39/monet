"""HITL interrupt coordinator — owns the pending Future for reply handoff."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from monet.cli.chat._hitl._text_parse import format_form_prompt, parse_text_reply

#: Transcript writer — any callable that takes a single pre-formatted line.
Writer = Callable[[str], None]

#: Busy-state toggle — drives the spinner + border pulse.
BusySetter = Callable[[bool], None]

#: Focus the prompt widget so the user knows where to type.
FocusPrompt = Callable[[], None]

#: Mount an inline widget form for *form*. Returns True when widgets mounted.
WidgetMounter = Callable[[dict[str, Any]], bool]

#: Tear down the widget form mounted by :data:`WidgetMounter`.
WidgetUnmounter = Callable[[], None]


class InterruptCoordinator:
    """Owns the ``Future`` used to hand a prompt submission to ``collect``.

    One coordinator per :class:`ChatApp`. ``consume_if_pending`` (text) and
    ``consume_payload`` (dict) both resolve the same pending future —
    whichever fires first wins. ``collect`` is called from within the
    turn loop when a HITL form is waiting.
    """

    def __init__(self) -> None:
        self._pending: asyncio.Future[str | dict[str, Any]] | None = None

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

    def consume_payload(self, payload: dict[str, Any]) -> bool:
        """Deliver a widget-built dict payload directly, bypassing text parse.

        Returns True when the coordinator was awaiting a reply.
        """
        pending = self._pending
        if pending is not None and not pending.done():
            self._pending = None
            pending.set_result(payload)
            return True
        return False

    async def collect(
        self,
        form: dict[str, Any],
        *,
        writer: Writer,
        busy_setter: BusySetter,
        focus_prompt: FocusPrompt,
        mount_widgets: WidgetMounter | None = None,
        unmount_widgets: WidgetUnmounter | None = None,
    ) -> dict[str, Any] | None:
        """Render *form* and parse the next user reply.

        When *mount_widgets* returns True, transcript text-prompt lines
        are suppressed in favour of the mounted widgets; the user can
        still type a reply as a text-parse fallback. Loops on parse
        failure so a typo (``aprove``) becomes a re-prompt rather than
        a silent reject.
        """
        used_widgets = bool(mount_widgets(form)) if mount_widgets else False
        if not used_widgets:
            for line in format_form_prompt(form):
                writer(line)
        try:
            first = True
            while True:
                if not first:
                    writer(
                        "[error] didn't recognise that — reply: "
                        "approve | revise <feedback> | reject"
                    )
                first = False
                busy_setter(False)
                loop = asyncio.get_running_loop()
                future: asyncio.Future[str | dict[str, Any]] = loop.create_future()
                self._pending = future
                focus_prompt()
                try:
                    reply = await future
                finally:
                    self._pending = None
                if isinstance(reply, dict):
                    busy_setter(True)
                    return reply
                payload = parse_text_reply(form, reply)
                if payload is not None:
                    busy_setter(True)
                    return payload
        finally:
            if used_widgets and unmount_widgets is not None:
                unmount_widgets()
