"""Transport protocol interfaces.

Defines the two runtime-checkable protocols — :class:`Session` and
:class:`TransportAdapter` — that every transport implementation must satisfy,
plus the :class:`ObservedEvent` wire type yielded by sessions.

Design constraints
------------------
- Protocols are ``@runtime_checkable`` so workload code can assert that a
  concrete adapter satisfies the interface without importing it.
- No runtime imports beyond stdlib + typing. Concrete adapters live in sibling
  modules and are imported only when instantiated.
- ``ObservedEvent`` carries only result events and transport-level observations.
  Progress, signals, and artifacts travel via the data plane gateway; they are
  never yielded here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from monet.worker.execution._protocol import Endpoint

__all__ = ["ObservedEvent", "Session", "TransportAdapter"]


@dataclass(frozen=True)
class ObservedEvent:
    """A single event observed on a transport session.

    Attributes:
        type: Event kind — ``"result"`` for the task outcome,
            ``"transport_error"`` for a non-fatal transport observation,
            or ``"transport_metric"`` for timing/throughput data.
        data: Arbitrary JSON-serialisable payload. For ``"result"`` events
            this is the raw agent result dict.
        timestamp: Monotonic timestamp (``time.monotonic()``) recorded when
            the event was received by the transport layer.
    """

    type: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.monotonic)


@runtime_checkable
class Session(Protocol):
    """An active connection to a running agent process.

    A session is opened by :meth:`TransportAdapter.connect` and must be
    closed when the workload is complete (success or failure).  The session
    lifetime maps to a single task execution.

    All methods are coroutines or async generators so the event loop is never
    blocked regardless of underlying I/O.

    Preconditions:
        :meth:`submit` must be called exactly once before :meth:`receive`.
        :meth:`cancel` and :meth:`close` are idempotent.
    """

    async def submit(self, payload: dict[str, Any]) -> None:
        """Send the task payload to the agent.

        Args:
            payload: JSON-serialisable task description. Must include at
                minimum ``task_id`` and ``payload`` keys.

        Raises:
            TransportError: If the payload cannot be delivered.
            ProtocolError: If the agent rejects the payload format.
        """
        ...

    def receive(self) -> AsyncIterator[ObservedEvent]:
        """Yield observed events until the session terminates.

        Yields events in arrival order.  The stream ends after a ``"result"``
        event is emitted or when the underlying connection closes.

        Yields:
            :class:`ObservedEvent` instances, typically one ``"result"`` event
            at the end of a successful execution.

        Raises:
            TransportError: If the connection breaks mid-stream.
            ProtocolError: If an event cannot be parsed.
            AgentError: If the agent sends an explicit error event.
        """
        ...

    async def cancel(self) -> None:
        """Request cancellation of the running task.

        Best-effort: transport implementations send the appropriate signal
        (SIGTERM for CLI, connection close for HTTP/SSE).  No guarantee that
        the agent process honours the request.

        Idempotent — safe to call multiple times or after close.
        """
        ...

    async def close(self) -> None:
        """Release all resources held by this session.

        Waits for the underlying stream/process to terminate before returning.
        Idempotent — safe to call multiple times.
        """
        ...


@runtime_checkable
class TransportAdapter(Protocol):
    """Factory that opens a :class:`Session` to a running agent.

    Each transport type (HTTP, CLI, SSE, MCP) provides one adapter.
    The adapter is stateless; the session carries per-execution state.

    Postcondition:
        :meth:`connect` returns a :class:`Session` that is ready to accept
        a :meth:`~Session.submit` call. The agent process must already be
        running at *endpoint* — the adapter does not start processes.
    """

    async def connect(self, endpoint: Endpoint) -> Session:
        """Open a session to the agent at *endpoint*.

        Args:
            endpoint: Describes where the agent is listening and how to
                reach it (address, process ID, backend type).

        Returns:
            An open :class:`Session` ready for :meth:`~Session.submit`.

        Raises:
            TransportError: If the agent cannot be reached.
        """
        ...
