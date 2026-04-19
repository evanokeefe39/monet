"""Transport helpers for the LangGraph SDK — public adapter API.

These wrap the ``langgraph_sdk`` client for thread management,
streaming, and state inspection. ``MonetClient`` is the canonical
consumer; the underscore prefix is historical — the symbols here are
stable for graph and tooling authors who need direct SDK access.

Stable API:

- :func:`make_client`, :func:`create_thread`
- :func:`stream_run`, :func:`drain_stream`, :func:`get_state_values`
- :func:`task_input`, :func:`chat_input`
- :data:`MONET_RUN_ID_KEY`, :data:`MONET_GRAPH_KEY`, :data:`MONET_CHAT_NAME_KEY`
- :data:`TRACE_CARRIER_METADATA_KEY`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from langgraph_sdk.client import LangGraphClient


def _classify_transport_error(exc: BaseException, url: str) -> None:
    """Re-raise *exc* as a typed MonetClientError when it is an httpx error."""
    try:
        import httpx
    except ImportError:
        return
    from monet.client._errors import ServerError, ServerUnreachable

    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout):
        raise ServerUnreachable(url, "connection refused or timed out") from exc
    if isinstance(exc, httpx.ReadTimeout):
        raise ServerUnreachable(url, "read timed out") from exc
    if isinstance(exc, httpx.RemoteProtocolError):
        raise ServerUnreachable(url, "server closed connection (restart?)") from exc
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            raise ServerError(status, "check MONET_API_KEY") from exc
        raise ServerError(status, exc.response.text[:200]) from exc


# ── Trace + thread metadata keys ────────────────────────────────────

TRACE_CARRIER_METADATA_KEY = "monet_trace_carrier"

MONET_RUN_ID_KEY = "monet_run_id"
MONET_GRAPH_KEY = "monet_graph"
MONET_CHAT_NAME_KEY = "monet_chat_name"


# ── Client factory ──────────────────────────────────────────────────


def make_client(
    url: str | None = None,
    *,
    api_key: str | None = None,
) -> LangGraphClient:
    """Create a LangGraph SDK client pointed at *url*.

    When *url* is ``None``, defaults to the server URL resolved by
    :class:`monet.config.ClientConfig` (``MONET_SERVER_URL`` env var, or
    ``http://localhost:{STANDARD_DEV_PORT}`` if unset). When *api_key* is
    ``None``, falls back to ``MONET_API_KEY`` via :class:`ClientConfig`.

    When a key is resolved (explicit or env), an ``Authorization: Bearer``
    header is sent on every request so monet's server-side middleware and
    custom routes can validate it. Unset keys cause no auth header to be
    sent — correct for local dev with auth disabled.
    """
    from langgraph_sdk import get_client

    from monet.config import ClientConfig

    cfg = ClientConfig.load()
    resolved_url = url if url is not None else cfg.server_url
    resolved_key = api_key if api_key is not None else cfg.api_key
    headers = {"Authorization": f"Bearer {resolved_key}"} if resolved_key else None
    return get_client(url=resolved_url, headers=headers)


async def create_thread(
    client: LangGraphClient,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Create a fresh server-side thread and return its id.

    Args:
        client: LangGraph SDK client.
        metadata: Optional metadata dict attached to the thread.
            Adapters tag threads with ``monet_run_id`` and ``monet_graph``
            so :meth:`MonetClient.list_runs` / :meth:`get_run` can find them.
    """
    url = str(getattr(client, "http_url", "") or "")
    kwargs: dict[str, Any] = {}
    if metadata:
        kwargs["metadata"] = metadata
    try:
        thread = await client.threads.create(**kwargs)
    except Exception as exc:
        _classify_transport_error(exc, url)
        raise
    return str(thread["thread_id"])


# ── Streaming ───────────────────────────────────────────────────────


async def stream_run(
    client: LangGraphClient,
    thread_id: str,
    graph_id: str,
    *,
    input: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Stream a run on *graph_id* and yield ``(mode, data)`` tuples.

    Pass either *input* (to start a new run) or *command* (to resume
    an interrupt).
    """
    kwargs: dict[str, Any] = {
        "stream_mode": ["updates", "custom"],
        # ``stream_subgraphs=True`` surfaces ``custom`` events emitted
        # inside subgraphs (e.g. ``emit_progress`` from agents running
        # under chat's execution subgraph). Without it, only top-level
        # graph events reach the client.
        "stream_subgraphs": True,
    }
    if command is not None:
        kwargs["command"] = command
    else:
        kwargs["input"] = input or {}
    if metadata:
        kwargs["metadata"] = metadata

    url = str(getattr(client, "http_url", "") or "")
    try:
        async for chunk in client.runs.stream(thread_id, graph_id, **kwargs):
            event = getattr(chunk, "event", None) or ""
            data = getattr(chunk, "data", None)
            if event.startswith("updates"):
                yield ("updates", data)
            elif event.startswith("custom"):
                yield ("custom", data)
            elif event.startswith("error"):
                yield ("error", data)
            elif event == "metadata":
                # Aegra emits run_id exactly once, up front. Clients use
                # the "metadata" mode to stamp run_id onto subsequent
                # updates (ADR-006 F2).
                yield ("metadata", data)
    except Exception as exc:
        _classify_transport_error(exc, url)
        raise


# ── State helpers ───────────────────────────────────────────────────


async def get_state_values(
    client: LangGraphClient,
    thread_id: str,
) -> tuple[dict[str, Any], list[str]]:
    """Return ``(values, next_nodes)`` for the current thread state.

    *next_nodes* is empty when the run completed normally and
    contains interrupt node names when the run is paused.
    """
    state = await client.threads.get_state(thread_id)
    raw = state.get("values") or {}
    values: dict[str, Any] = raw if isinstance(raw, dict) else {}
    nxt = list(state.get("next") or [])
    return values, nxt


async def drain_stream(
    client: LangGraphClient,
    thread_id: str,
    graph_id: str,
    *,
    input: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
    trace_carrier: dict[str, str] | None = None,
    on_event: Callable[[str, Any], None] | None = None,
) -> None:
    """Stream a run to completion, optionally dispatching events.

    *on_event* receives ``(mode, data)`` for each chunk.
    """
    meta = {TRACE_CARRIER_METADATA_KEY: trace_carrier} if trace_carrier else None
    async for mode, data in stream_run(
        client,
        thread_id,
        graph_id,
        input=input,
        command=command,
        metadata=meta,
    ):
        if mode == "error":
            from monet.client._errors import ServerError

            raise ServerError(None, str(data))
        if on_event is not None:
            on_event(mode, data)


# ── State initializers ──────────────────────────────────────────────


def task_input(task: str, run_id: str) -> dict[str, Any]:
    """Build the initial state dict for any entry-like graph.

    Canonical form: ``{task, trace_id, run_id}`` with
    ``trace_id = "trace-{run_id}"`` — orchestration OpenTelemetry spans
    rely on this prefix for trace continuity.
    """
    return {
        "task": task,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
    }


def chat_input(message: str) -> dict[str, Any]:
    """Build the input state dict for the chat graph."""
    return {
        "messages": [{"role": "user", "content": message}],
    }
