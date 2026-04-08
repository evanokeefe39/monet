"""LangGraph SDK client wiring.

Owns the only ``langgraph_sdk.get_client(...)`` call site in the
example. Provides:

  - ``make_client``      build the SDK client
  - ``create_thread``    one-liner thread creator
  - ``stream_run``       async iterator that adapts the SDK's stream
                         shape into the ``(mode, data)`` tuple format
                         consumed by ``display.print_streaming_event``
                         and ``workflow``'s interrupt detection.

The streaming helper is the only piece doing any adapter work — the SDK
yields ``StreamPart`` objects with ``.event`` / ``.data`` attributes,
where the ``.event`` string carries channel information like
``"updates"``, ``"custom"``, ``"messages"``, ``"values"``, or
``"error"``. We collapse it back to ``(mode, data)`` so callers don't
need to know about ``StreamPart``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph_sdk import get_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph_sdk.client import LangGraphClient


def make_client(url: str = "http://localhost:2024") -> LangGraphClient:
    """Construct a LangGraph SDK client pointed at ``url``."""
    return get_client(url=url)


async def create_thread(client: LangGraphClient) -> str:
    """Create a fresh server-side thread and return its id."""
    thread = await client.threads.create()
    return str(thread["thread_id"])


async def stream_run(
    client: LangGraphClient,
    thread_id: str,
    graph_id: str,
    *,
    input: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Stream a run on ``graph_id`` and yield ``(mode, data)`` tuples.

    Pass either ``input`` (to start a new run) or ``command`` (to resume
    an interrupt). The two are mutually exclusive.

    The SDK's ``client.runs.stream(...)`` returns ``StreamPart`` objects
    whose ``.event`` field encodes which stream channel each chunk came
    from. We expose only the channels the example actually consumes:
    ``updates`` (node-level state diffs) and ``custom`` (events emitted
    via ``emit_progress``).
    """
    kwargs: dict[str, Any] = {
        "stream_mode": ["updates", "custom"],
    }
    if command is not None:
        kwargs["command"] = command
    else:
        kwargs["input"] = input or {}

    async for chunk in client.runs.stream(thread_id, graph_id, **kwargs):
        event = getattr(chunk, "event", None) or ""
        data = getattr(chunk, "data", None)
        # The SDK reports interrupts as their own event channel — surface
        # it under a stable name so workflow.py can match on it.
        if event.startswith("updates"):
            yield ("updates", data)
        elif event.startswith("custom"):
            yield ("custom", data)
        elif event.startswith("error"):
            yield ("error", data)
        # Ignore values/messages/metadata channels — not consumed here.
