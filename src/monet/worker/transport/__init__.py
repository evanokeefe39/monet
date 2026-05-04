"""Transport adapter protocols and implementations.

Transport adapters connect a running agent process to the worker's workload
execution layer. Each adapter speaks a specific protocol (HTTP, CLI, SSE,
or MCP) and yields :class:`ObservedEvent` instances to the workload layer.
"""

from __future__ import annotations

from ._cli import CLISession, CLITransport
from ._errors import AgentError, ProtocolError, TransportError
from ._http import HTTPSession, HTTPTransport
from ._protocol import ObservedEvent, Session, TransportAdapter
from ._sse import SSESession, SSETransport

__all__ = [
    "AgentError",
    "CLISession",
    "CLITransport",
    "HTTPSession",
    "HTTPTransport",
    "ObservedEvent",
    "ProtocolError",
    "SSESession",
    "SSETransport",
    "Session",
    "TransportAdapter",
    "TransportError",
]
