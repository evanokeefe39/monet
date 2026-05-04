"""SSE (Server-Sent Events) transport adapter.

POSTs the task payload to ``{endpoint.address}/task`` with
``Accept: text/event-stream``, then reads the response as a streaming
Server-Sent Events body.  Each ``data:`` line must be a JSON object
matching the :class:`~._protocol.ObservedEvent` schema.  The stream
terminates after a ``"result"`` event or when the connection closes.

SSE framing handled here (``data:`` prefix extraction only).  The
``id:`` and ``event:`` SSE fields are not used; type information lives
inside the JSON payload.

Error classification:
    ``TransportError``: connection refused, timeout, DNS failure.
    ``ProtocolError``: ``data:`` payload is not valid JSON or not a JSON object.
    ``AgentError``: HTTP 4xx/5xx, or agent emits ``{"type": "error", ...}``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

from ._errors import AgentError, ProtocolError, TransportError
from ._protocol import ObservedEvent

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from monet.worker.execution._protocol import Endpoint

__all__ = ["SSESession", "SSETransport"]


class SSESession:
    """Streaming SSE session for a single task execution."""

    def __init__(self, client: httpx.AsyncClient, endpoint: Endpoint) -> None:
        self._client = client
        self._endpoint = endpoint
        self._response: httpx.Response | None = None
        self._closed = False

    async def submit(self, payload: dict[str, Any]) -> None:
        """POST *payload* and open the SSE response stream.

        Uses ``httpx`` streaming mode so the response body is not buffered
        before :meth:`receive` reads it.

        Args:
            payload: JSON-serialisable task description.

        Raises:
            TransportError: Connection-level failure.
            AgentError: HTTP 4xx or 5xx status code.
        """
        url = self._endpoint.address.rstrip("/") + "/task"
        request = self._client.build_request(
            "POST",
            url,
            json=payload,
            headers={"Accept": "text/event-stream"},
        )
        try:
            self._response = await self._client.send(request, stream=True)
        except httpx.ConnectError as exc:
            raise TransportError(f"connection refused: {url}") from exc
        except httpx.TimeoutException as exc:
            raise TransportError(f"timeout connecting to {url}") from exc
        except httpx.HTTPError as exc:
            raise TransportError(str(exc)) from exc

        if self._response.status_code >= 400:
            body = await self._response.aread()
            raise AgentError(
                f"HTTP {self._response.status_code}: {body.decode()[:200]}"
            )

    def receive(self) -> AsyncIterator[ObservedEvent]:
        """Yield ``ObservedEvent`` instances from SSE ``data:`` lines.

        The stream terminates after a ``"result"`` event or when the
        connection closes.

        Yields:
            :class:`ObservedEvent` per non-empty ``data:`` line.

        Raises:
            ProtocolError: If ``submit()`` was not called first, or a
                ``data:`` payload is not valid JSON or not a JSON object.
            AgentError: If the agent emits ``{"type": "error"}``.
        """
        return self._iter_sse()

    async def _iter_sse(self) -> AsyncGenerator[ObservedEvent, None]:
        if self._response is None:
            raise ProtocolError("submit() must be called before receive()")

        async for raw_line in self._response.aiter_lines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue

            data_str = line[len("data:") :].strip()
            if not data_str:
                continue

            try:
                obj = json.loads(data_str)
            except json.JSONDecodeError as exc:
                raise ProtocolError(f"SSE data is not valid JSON: {exc}") from exc

            if not isinstance(obj, dict):
                raise ProtocolError(
                    f"SSE event is not a JSON object: {data_str[:100]!r}"
                )

            event_type: str = obj.get("type", "result")

            if event_type == "error":
                raise AgentError(obj.get("message", str(obj)))

            data = obj.get("data", obj)
            event_data: dict[str, Any] = data if isinstance(data, dict) else obj
            yield ObservedEvent(type=event_type, data=event_data)

            if event_type == "result":
                break

    async def cancel(self) -> None:
        """Close the HTTP connection to abort the SSE stream.

        Idempotent — safe to call after ``close()``.
        """
        if self._response is not None and not self._closed:
            await self._response.aclose()

    async def close(self) -> None:
        """Close the SSE response stream and the underlying httpx client.

        Idempotent.
        """
        if self._closed:
            return
        self._closed = True
        if self._response is not None:
            await self._response.aclose()
        await self._client.aclose()


class SSETransport:
    """Opens SSE sessions to agents that stream results via Server-Sent Events."""

    async def connect(self, endpoint: Endpoint) -> SSESession:
        """Create an SSE session to the agent at *endpoint*.

        Args:
            endpoint: Agent network address. ``endpoint.address`` must be an
                ``http://`` or ``https://`` URL.

        Returns:
            :class:`SSESession` ready for :meth:`~SSESession.submit`.

        Raises:
            TransportError: If the agent cannot be reached.
        """
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        )
        return SSESession(client, endpoint)
