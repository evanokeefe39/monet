"""Stream adapter: convert raw RunEvent iterator to deduplicated TUIEvents.

LangGraph with ``subgraphs=True`` re-emits subgraph events from the parent
namespace. :class:`StreamAdapter` deduplicates by event identity hash so the
TUI never renders the same logical event twice.

``AssistantChunk`` events are never deduped — text streams have sequence
semantics and identical text in consecutive chunks is intentional.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from monet.client._events import (
    AgentProgress,
    Interrupt,
    NodeUpdate,
    RunComplete,
    RunFailed,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator


@dataclass(frozen=True)
class AssistantChunk:
    """Text content destined for the ``[assistant]`` transcript line."""

    text: str


TUIEvent = AssistantChunk | AgentProgress | Interrupt | RunComplete | RunFailed
"""Union of all event types the chat TUI renders from a run stream."""


class StreamAdapter:
    """Map a raw run-event stream to deduplicated :data:`TUIEvent` items.

    One adapter instance owns one stream's dedup state — create a fresh
    instance per run.
    """

    def __init__(self) -> None:
        self._seen: set[tuple[Any, ...]] = set()

    async def adapt(self, stream: AsyncIterator[Any]) -> AsyncGenerator[TUIEvent, None]:
        """Yield :data:`TUIEvent` items from *stream*, deduplicating subgraph
        re-emits.

        Precondition: *stream* is an async iterable of raw run events.
        Postcondition: each non-``AssistantChunk`` event is emitted at most once
        per adapter instance; ``AssistantChunk`` events are always forwarded.
        """
        async for event in stream:
            tui_event = _to_tui_event(event)
            if tui_event is None:
                continue
            if not isinstance(tui_event, AssistantChunk):
                key = _dedup_key(tui_event)
                if key in self._seen:
                    continue
                self._seen.add(key)
            yield tui_event


def _dedup_key(event: TUIEvent) -> tuple[Any, ...]:
    """Return a hashable identity key for dedup. Excludes dict fields."""
    if isinstance(event, AgentProgress):
        return (
            AgentProgress,
            event.run_id,
            event.agent_id,
            event.status,
            event.command,
            event.reasons,
        )
    if isinstance(event, Interrupt):
        return (Interrupt, event.run_id, event.tag)
    if isinstance(event, RunComplete):
        return (RunComplete, event.run_id)
    if isinstance(event, RunFailed):
        return (RunFailed, event.run_id, event.error)
    # AssistantChunk is excluded from dedup — callers guard with isinstance check.
    return (type(event), id(event))


def _to_tui_event(event: Any) -> TUIEvent | None:
    """Map one raw event to a :data:`TUIEvent`, or ``None`` to discard."""
    if isinstance(event, AgentProgress | Interrupt | RunComplete | RunFailed):
        return event
    if isinstance(event, str):
        return AssistantChunk(text=event)
    if isinstance(event, NodeUpdate):
        msgs = event.update.get("messages")
        if msgs and isinstance(msgs, list):
            last = msgs[-1]
            content: str = ""
            if isinstance(last, dict):
                content = str(last.get("content") or "")
            elif hasattr(last, "content"):
                content = str(last.content or "")
            if content:
                return AssistantChunk(text=content)
    return None
