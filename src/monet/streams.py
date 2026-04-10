"""AgentStream — typed async event bus for external agents.

The translation boundary between an external agent's output and the SDK
primitive layer. Reads a stream (subprocess stdout, SSE, HTTP polling),
parses typed JSON events, dispatches handlers as events arrive.

Subclass and override ``_iter_events`` for transports beyond the bundled
constructors (e.g. gRPC).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import shlex
from typing import TYPE_CHECKING, Any

from .core.catalogue import get_catalogue
from .core.stubs import emit_progress, emit_signal
from .exceptions import SemanticError
from .signals import SignalType
from .types import Signal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    Handler = Callable[[dict[str, Any]], None | Awaitable[None]]


_KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {"progress", "signal", "artifact", "result", "error"}
)


class AgentStream:
    """Typed async event bus over an external agent's output.

    Construct via a named transport (``cli``/``sse``/``http``), register
    handlers with ``.on()``, then ``await .run()``. Default handlers wire
    progress → ``emit_progress``, artifact → catalogue, signal →
    ``emit_signal``, error → ``SemanticError``, result → return value.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._iter_factory: Callable[[], AsyncIterator[dict[str, Any]]] | None = None

    # ── named constructors ────────────────────────────────────────────────────

    @classmethod
    def cli(cls, cmd: list[str], **kwargs: Any) -> AgentStream:
        """Stream newline-delimited JSON from a subprocess's stdout."""
        stream = cls()
        stream._iter_factory = lambda: _iter_subprocess(cmd, **kwargs)
        return stream

    @classmethod
    def sse(cls, url: str, **kwargs: Any) -> AgentStream:
        """Stream Server-Sent Events from ``url``."""
        stream = cls()
        stream._iter_factory = lambda: _iter_sse(url, **kwargs)
        return stream

    @classmethod
    def http(cls, url: str, interval: float = 1.0, **kwargs: Any) -> AgentStream:
        """Poll ``url`` every ``interval`` seconds for new JSON events."""
        stream = cls()
        stream._iter_factory = lambda: _iter_http_poll(url, interval, **kwargs)
        return stream

    @classmethod
    def grpc(cls, *args: Any, **kwargs: Any) -> AgentStream:
        """Reserved. Subclass AgentStream and override ``_iter_events``."""
        msg = (
            "AgentStream.grpc() is reserved. Subclass AgentStream and override "
            "_iter_events() to integrate a gRPC streaming method."
        )
        raise NotImplementedError(msg)

    # ── handler registration ──────────────────────────────────────────────────

    def on(self, event_type: str, handler: Handler) -> AgentStream:
        """Register a handler for ``event_type``. Returns self for chaining.

        Multiple handlers per event type are called in registration order.
        Sync handlers are invoked directly; async handlers are awaited.
        """
        self._handlers.setdefault(event_type, []).append(handler)
        return self

    # ── execution ─────────────────────────────────────────────────────────────

    async def run(self) -> str | None:
        """Iterate events to completion. Returns the last ``result.output``."""
        result_value: str | None = None
        async for event in self._iter_events():
            event_type = event.get("type")
            if not isinstance(event_type, str):
                msg = f"Stream event missing 'type': {event!r}"
                raise ValueError(msg)

            # Validate signal events before any handler fires.
            if event_type == "signal":
                raw = event.get("signal_type", "")
                if raw not in {s.value for s in SignalType}:
                    msg = (
                        f"Unknown signal_type {raw!r} from external agent. "
                        "Version mismatch between binary and SDK."
                    )
                    raise ValueError(msg)

            handlers = self._handlers.get(event_type)
            if handlers:
                for handler in handlers:
                    out = handler(event)
                    if inspect.isawaitable(out):
                        await out
            else:
                await self._default_handler(event_type, event)

            if event_type == "result":
                output = event.get("output")
                result_value = output if isinstance(output, str) else None

        return result_value

    async def _iter_events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield event dicts from the underlying transport.

        Default delegates to the factory set by a named constructor.
        Subclasses may override directly.
        """
        if self._iter_factory is None:
            msg = (
                "AgentStream has no transport. Use AgentStream.cli/.sse/.http "
                "or subclass and override _iter_events()."
            )
            raise RuntimeError(msg)
        async for event in self._iter_factory():
            yield event

    async def _default_handler(self, event_type: str, event: dict[str, Any]) -> None:
        if event_type == "progress":
            emit_progress(event)
        elif event_type == "signal":
            emit_signal(
                Signal(
                    type=event.get("signal_type", ""),
                    reason=event.get("reason", ""),
                    metadata=event.get("metadata"),
                )
            )
        elif event_type == "artifact":
            content = event.get("content", "")
            content_bytes = (
                content.encode() if isinstance(content, str) else bytes(content)
            )
            await get_catalogue().write(
                content=content_bytes,
                content_type=event.get("content_type", "application/octet-stream"),
                summary=event.get("summary", ""),
                confidence=float(event.get("confidence", 0.0)),
                completeness=event.get("completeness", "complete"),
                sensitivity_label=event.get("sensitivity_label", "internal"),
            )
        elif event_type == "error":
            raise SemanticError(
                type=event.get("error_type", "stream_error"),
                message=event.get("message", "external agent reported error"),
            )
        elif event_type == "result":
            return
        else:
            import logging

            logging.getLogger("monet.streams").warning(
                "unknown event type from stream: %s", event_type
            )


# ── transport iterators ───────────────────────────────────────────────────────


async def _iter_subprocess(
    cmd: list[str], **kwargs: Any
) -> AsyncIterator[dict[str, Any]]:
    """Spawn ``cmd`` and yield one parsed JSON object per stdout line."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    assert proc.stdout is not None
    try:
        async for line in proc.stdout:
            text = line.decode().strip()
            if not text:
                continue
            try:
                yield json.loads(text)
            except json.JSONDecodeError as exc:
                msg = f"Invalid JSON line from {shlex.join(cmd)}: {text!r}"
                raise ValueError(msg) from exc
    finally:
        return_code = await proc.wait()
    if return_code != 0:
        stderr = b""
        if proc.stderr is not None:
            stderr = await proc.stderr.read()
        raise SemanticError(
            type="subprocess_failed",
            message=(
                f"{shlex.join(cmd)} exited with {return_code}: "
                f"{stderr.decode(errors='replace')[:500]}"
            ),
        )


async def _iter_sse(url: str, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
    """Stream JSON events from an SSE endpoint."""
    import httpx

    async with (
        httpx.AsyncClient(timeout=None) as client,
        client.stream("GET", url, **kwargs) as response,
    ):
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if not payload:
                continue
            yield json.loads(payload)


async def _iter_http_poll(
    url: str, interval: float, *, max_polls: int = 300, **kwargs: Any
) -> AsyncIterator[dict[str, Any]]:
    """Poll ``url`` every ``interval`` seconds; stop on a ``result`` event.

    Raises ``TimeoutError`` if ``max_polls`` iterations are exhausted
    without receiving a ``result`` event.
    """
    import httpx

    async with httpx.AsyncClient() as client:
        for _ in range(max_polls):
            response = await client.get(url, **kwargs)
            response.raise_for_status()
            event = response.json()
            yield event
            if event.get("type") == "result":
                return
            await asyncio.sleep(interval)
        raise TimeoutError(
            f"HTTP poll at {url} did not produce a result event after {max_polls} polls"
        )


__all__ = ["AgentStream"]
