"""Transport-layer error hierarchy.

Three distinct failure modes:

- :class:`TransportError` — the connection itself failed (refused, timed out,
  network unreachable). The agent process may still be running or may never
  have started. Retry may succeed after a delay.

- :class:`ProtocolError` — the connection succeeded but the agent sent
  malformed data (bad JSON, missing required field, unexpected stream end).
  Retrying the same agent version is unlikely to help without a fix.

- :class:`AgentError` — the agent responded with an explicit error payload
  (non-2xx HTTP, structured error event). The agent ran and reported failure.
  The error body carries agent-supplied context.
"""

from __future__ import annotations


class TransportError(Exception):
    """Connection-level failure.

    Raised when the transport cannot reach the agent process at all:
    connection refused, TCP timeout, DNS failure, etc.
    """


class ProtocolError(Exception):
    """Protocol-level failure.

    Raised when the agent is reachable but its response cannot be parsed or
    does not conform to the expected event schema.
    """


class AgentError(Exception):
    """Agent-reported failure.

    Raised when the agent process responded with an explicit error — e.g.
    HTTP 4xx/5xx, or an event with ``type="error"``.  The string form of
    this exception is the agent's error message.
    """
