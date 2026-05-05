"""Pydantic models for the monet adapter wire protocol.

Adapters (external HTTP servers wrapping agent processes) must conform to
this schema.  See ``docs/guides/adapter-protocol.md`` for the full spec.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AdapterTaskRequest(BaseModel):
    """Body POSTed by the worker to ``/task``.

    Precondition:
        ``task_id`` is a non-empty string unique within a worker run.
        ``payload`` contains at minimum a ``task`` or ``command`` key.
    """

    task_id: str
    payload: dict[str, Any]


class AdapterTaskResponse(BaseModel):
    """200 response body from ``/task`` on success.

    Postcondition:
        ``success`` is ``True``.
        ``output`` is the agent's primary text result.
        ``artifacts`` carries any named byte payloads the adapter wants to
        surface — keys are artifact names, values are string content.
    """

    output: str
    success: bool = True
    artifacts: dict[str, str] = Field(default_factory=dict)


class AdapterErrorResponse(BaseModel):
    """4xx/5xx response body from ``/task`` on failure.

    ``error_code`` values:
        ``INVALID_REQUEST``  — request body missing or malformed.
        ``AGENT_ERROR``      — agent/backend raised an exception.
        ``UPSTREAM_ERROR``   — adapter's upstream dependency (e.g. Pi, LLM
                               provider) returned a non-2xx response.
        ``NOT_READY``        — adapter received a task before init completed
                               (should not occur if /health is polled first).
    """

    error: str
    error_code: str = "AGENT_ERROR"
