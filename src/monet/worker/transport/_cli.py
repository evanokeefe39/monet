"""CLI transport adapter.

Communicates with an agent via subprocess stdin/stdout.  The subprocess is
spawned during ``submit()`` using the command in ``endpoint.metadata["cmd"]``.
The session writes the JSON payload to stdin, reads JSON event objects from
stdout line by line, and terminates the process in ``cancel()``/``close()``.

Each stdout line emitted by the agent must be a JSON object.  The schema:

.. code-block:: json

    {"type": "result", "data": {...}}    # terminal — ends the stream
    {"type": "transport_metric", "data": {...}}  # non-terminal observation
    {"type": "error", "message": "..."}  # raises AgentError

``type`` defaults to ``"result"`` when absent.

Error classification:
    ``TransportError``: command not found, stdin pipe broken, spawn failure.
    ``ProtocolError``: stdout line is not valid JSON or not a JSON object.
    ``AgentError``: agent emits ``{"type": "error", ...}``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

from ._errors import AgentError, ProtocolError, TransportError
from ._protocol import ObservedEvent

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from monet.worker.execution._protocol import Endpoint

__all__ = ["CLISession", "CLITransport"]

_SIGKILL_GRACE_S: float = 5.0


class CLISession:
    """Subprocess-backed session for a single task execution.

    The subprocess lifetime matches the session lifetime: it is spawned in
    ``submit()`` and reaped in ``close()``.

    Preconditions:
        ``endpoint.metadata["cmd"]`` must be a non-empty list of strings.
    """

    def __init__(self, endpoint: Endpoint) -> None:
        self._endpoint = endpoint
        self._proc: asyncio.subprocess.Process | None = None
        self._closed = False

    async def submit(self, payload: dict[str, Any]) -> None:
        """Spawn the subprocess and write *payload* as JSON to stdin.

        Closes stdin after writing so the agent receives EOF.

        Args:
            payload: JSON-serialisable task description.

        Raises:
            TransportError: Spawn failure or stdin pipe error.
        """
        cmd: list[str] = self._endpoint.metadata.get("cmd", [])
        if not cmd:
            raise TransportError(
                "endpoint.metadata['cmd'] is required for CLI transport"
            )

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise TransportError(f"command not found: {cmd[0]!r}") from exc
        except OSError as exc:
            raise TransportError(f"failed to spawn subprocess: {exc}") from exc

        assert self._proc.stdin is not None
        try:
            line = json.dumps(payload).encode() + b"\n"
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
            self._proc.stdin.close()
            await self._proc.stdin.wait_closed()
        except BrokenPipeError as exc:
            raise TransportError("subprocess stdin closed unexpectedly") from exc

    def receive(self) -> AsyncIterator[ObservedEvent]:
        """Yield ``ObservedEvent`` instances from stdout JSON lines.

        The stream terminates after a ``"result"`` event or when stdout closes.

        Yields:
            :class:`ObservedEvent` per stdout line until the result event.

        Raises:
            ProtocolError: If ``submit()`` was not called first, or a line is
                not valid JSON or not a JSON object.
            AgentError: If the agent emits an ``{"type": "error"}`` event.
        """
        return self._iter_stdout()

    async def _iter_stdout(self) -> AsyncGenerator[ObservedEvent, None]:
        if self._proc is None:
            raise ProtocolError("submit() must be called before receive()")

        assert self._proc.stdout is not None
        async for raw_line in self._proc.stdout:
            line = raw_line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProtocolError(f"stdout line is not valid JSON: {exc}") from exc

            if not isinstance(obj, dict):
                raise ProtocolError(
                    f"stdout event is not a JSON object: {line[:100]!r}"
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
        """Send SIGTERM; escalate to SIGKILL after the grace period.

        Idempotent — safe to call after the process has already exited.
        """
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            self._proc.terminate()
        except ProcessLookupError:
            return

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=_SIGKILL_GRACE_S)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()

    async def close(self) -> None:
        """Ensure the subprocess has exited and release resources.

        Calls ``cancel()`` if the process is still running.  Idempotent.
        """
        if self._closed:
            return
        self._closed = True

        if self._proc is not None and self._proc.returncode is None:
            await self.cancel()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)


class CLITransport:
    """Opens CLI sessions to agents that read JSON from stdin and write to stdout."""

    async def connect(self, endpoint: Endpoint) -> CLISession:
        """Create a CLI session for the agent described by *endpoint*.

        The subprocess is NOT spawned here; spawning is deferred to
        :meth:`~CLISession.submit` so the process lifetime is bounded by the
        task execution, not the session object lifetime.

        Args:
            endpoint: Must have ``metadata["cmd"]`` — the command to invoke.

        Returns:
            :class:`CLISession` ready for :meth:`~CLISession.submit`.
        """
        return CLISession(endpoint)
