"""HTTP transport adapter.

Sends a single POST to ``{endpoint.address}/task`` and reads the JSON
result from the response body.  The full response is buffered during
``submit()``; ``receive()`` yields the single result event synchronously.

Error classification:
    ``TransportError``: connection refused, timeout, DNS failure.
    ``ProtocolError``: response body is not valid JSON.
    ``AgentError``: HTTP 4xx or 5xx response.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError

from ._errors import AgentError, ProtocolError, TransportError
from ._protocol import ObservedEvent
from ._schemas import AdapterErrorResponse, AdapterTaskResponse

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from monet.worker.execution._protocol import Endpoint

__all__ = ["HTTPSession", "HTTPTransport"]


class HTTPSession:
    """Single-request HTTP session for one task execution."""

    def __init__(self, client: httpx.AsyncClient, endpoint: Endpoint) -> None:
        self._client = client
        self._endpoint = endpoint
        self._result: dict[str, Any] | None = None
        self._closed = False

    async def submit(self, payload: dict[str, Any]) -> None:
        """POST *payload* to ``{endpoint.address}/task`` and buffer the result.

        Args:
            payload: JSON-serialisable task description.

        Raises:
            TransportError: Connection-level failure.
            ProtocolError: Response body is not valid JSON.
            AgentError: HTTP 4xx or 5xx status code.
        """
        url = self._endpoint.address.rstrip("/") + "/task"
        try:
            response = await self._client.post(url, json=payload)
        except httpx.ConnectError as exc:
            raise TransportError(f"connection refused: {url}") from exc
        except httpx.TimeoutException as exc:
            raise TransportError(f"timeout connecting to {url}") from exc
        except httpx.HTTPError as exc:
            raise TransportError(str(exc)) from exc

        if response.status_code >= 400:
            body = response.text
            _log.debug(
                "agent error response (status=%d, url=%s): %s",
                response.status_code,
                url,
                body,
            )
            try:
                err = AdapterErrorResponse.model_validate_json(body)
                message = f"HTTP {response.status_code} [{err.error_code}]: {err.error}"
            except (ValidationError, json.JSONDecodeError):
                message = f"HTTP {response.status_code}: {body}"
            raise AgentError(message, status_code=response.status_code, body=body)

        try:
            raw = response.json()
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"response is not valid JSON: {exc}") from exc

        if not isinstance(raw, dict):
            raise ProtocolError(f"response JSON is not an object: {type(raw).__name__}")

        try:
            parsed = AdapterTaskResponse.model_validate(raw)
            self._result = parsed.model_dump()
        except ValidationError as exc:
            _log.debug("adapter response failed schema validation: %s", exc)
            # Accept non-conforming responses — raise ProtocolError only on
            # fields that make the result unusable (missing output).
            if "output" not in raw:
                raise ProtocolError(
                    f"adapter response missing required 'output' field: {raw}"
                ) from exc
            self._result = raw

    def receive(self) -> AsyncIterator[ObservedEvent]:
        """Yield the single result event buffered by ``submit()``.

        Yields:
            One ``ObservedEvent`` with ``type="result"``.

        Raises:
            ProtocolError: If ``submit()`` has not been called.
        """
        return self._iter_result()

    async def _iter_result(self) -> AsyncGenerator[ObservedEvent, None]:
        if self._result is None:
            raise ProtocolError("submit() must be called before receive()")
        yield ObservedEvent(type="result", data=self._result)

    async def cancel(self) -> None:
        """No-op — HTTP is request-response; the request was already sent."""

    async def close(self) -> None:
        """Close the underlying httpx client."""
        if not self._closed:
            self._closed = True
            await self._client.aclose()


class HTTPTransport:
    """Opens HTTP sessions to agents that expose a ``/task`` endpoint."""

    async def connect(self, endpoint: Endpoint) -> HTTPSession:
        """Create an HTTP session to the agent at *endpoint*.

        Args:
            endpoint: Agent network address. ``endpoint.address`` must be an
                ``http://`` or ``https://`` URL.

        Returns:
            ``HTTPSession`` ready for :meth:`~HTTPSession.submit`.

        Raises:
            TransportError: If the agent cannot be reached.
        """
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        )
        return HTTPSession(client, endpoint)
