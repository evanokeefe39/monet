"""Chat-specific client operations extracted from :class:`MonetClient`.

:class:`ChatClient` handles chat CRUD (create, list, history) plus the
streaming turn loop (send_message, resume_chat, get_chat_interrupt). It
is composed inside :class:`~monet.client.MonetClient` as ``client.chat``
so run-lifecycle and introspection concerns stay in the parent class.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from monet.client._errors import ServerError
from monet.client._events import AgentProgress, ChatSummary, ThreadRun
from monet.client._wire import (
    MONET_CHAT_NAME_KEY,
    MONET_GRAPH_KEY,
    chat_input,
    create_thread,
    get_state_values,
    stream_run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph_sdk.client import LangGraphClient

_log = logging.getLogger("monet.client.chat")


def _extract_interrupt_payload(state: Any) -> dict[str, Any]:
    """Pull the first interrupt payload off a LangGraph state snapshot."""

    def _get(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    tasks = _get(state, "tasks") or []
    for task in tasks:
        interrupts = _get(task, "interrupts") or []
        for interrupt_item in interrupts:
            value = _get(interrupt_item, "value")
            if isinstance(value, dict):
                return value
    values = _get(state, "values") or {}
    if isinstance(values, dict):
        fallback = values.get("__interrupt__")
        if isinstance(fallback, dict):
            return fallback
    return {}


def _build_agent_progress(run_id: str, data: dict[str, Any]) -> AgentProgress | None:
    """Convert a custom-stream wire dict into an :class:`AgentProgress`."""
    agent = data.get("agent", "")
    if not agent:
        return None
    return AgentProgress(
        run_id=run_id,
        agent_id=agent,
        status=data.get("status", ""),
        command=data.get("command", ""),
        reasons=data.get("reasons", ""),
    )


class ChatClient:
    """Chat-specific operations: CRUD, streaming turns, interrupts.

    Constructed by :class:`~monet.client.MonetClient` and accessible via
    ``client.chat``. All methods drive the graph identified by
    ``chat_graph_id`` (resolved from ``monet.toml [graphs]`` via
    :class:`MonetClient`).
    """

    def __init__(
        self,
        lg_client: LangGraphClient,
        *,
        chat_graph_id: str,
        base_url: str = "",
        api_key: str | None = None,
    ) -> None:
        self._client = lg_client
        self._chat_graph_id = chat_graph_id
        self._url = base_url
        self._api_key = api_key

    async def create_chat(self, *, name: str | None = None) -> str:
        """Create a new chat thread and return its thread_id."""
        metadata: dict[str, Any] = {MONET_GRAPH_KEY: self._chat_graph_id}
        if name:
            metadata[MONET_CHAT_NAME_KEY] = name
        return await create_thread(self._client, metadata=metadata)

    async def list_chats(self, *, limit: int = 20) -> list[ChatSummary]:
        """List recent chat sessions sorted by last activity."""
        threads = await self._client.threads.search(
            metadata={MONET_GRAPH_KEY: self._chat_graph_id},
            limit=limit,
            sort_by="updated_at",
            sort_order="desc",
        )
        summaries: list[ChatSummary] = []
        for t in threads:
            meta = t.get("metadata") or {}
            tid = str(t.get("thread_id", ""))
            name = str(meta.get(MONET_CHAT_NAME_KEY, ""))
            msg_count = 0
            try:
                values, _ = await get_state_values(self._client, tid)
                messages = values.get("messages") or []
                msg_count = len(messages)
            except Exception:
                _log.debug("list_chats message count failed for %s", tid, exc_info=True)
            summaries.append(
                ChatSummary(
                    thread_id=tid,
                    name=name,
                    message_count=msg_count,
                    created_at=str(t.get("created_at", "")),
                    updated_at=str(t.get("updated_at", "")),
                )
            )
        return summaries

    async def send_message(
        self, thread_id: str, message: str
    ) -> AsyncIterator[str | AgentProgress]:
        """Send a user message and yield response tokens."""
        # Eager commitment: attempt to update state with the user message first.
        # This creates a deterministic checkpoint that serves as the parent of the run.
        try:
            await self._client.threads.update_state(thread_id, chat_input(message))
            stream_input = None
        except Exception as exc:
            # If the thread is brand new, it has no associated graph schema yet,
            # so update_state fails. In this case, we fall back to the atomic
            # 'input' pattern for the first turn to 'seed' the association.
            if "no associated graph" in str(exc).lower():
                _log.debug("Unbound thread detected, falling back to atomic input")
                stream_input = chat_input(message)
            else:
                raise

        async for chunk in self._stream_chat_with_input(thread_id, input=stream_input):
            yield chunk

    async def _stream_chat_with_input(
        self,
        thread_id: str,
        *,
        input: dict[str, Any] | None = None,
        command: dict[str, Any] | None = None,
    ) -> AsyncIterator[str | AgentProgress]:
        async for mode, data in stream_run(
            self._client,
            thread_id,
            self._chat_graph_id,
            input=input,
            command=command,
        ):
            if mode == "error":
                raise ServerError(None, str(data))
            if mode == "custom":
                _log.debug("custom event type=%s data=%r", type(data).__name__, data)
                if isinstance(data, dict):
                    rid = data.get("run_id", "")
                    if not rid:
                        _log.debug("progress event missing run_id: %s", data)
                    progress = _build_agent_progress(rid, data)
                    if progress is not None:
                        yield progress
                continue
            if mode == "updates" and isinstance(data, dict):
                patches: list[Any] = []
                if "messages" in data:
                    patches.append(data)
                patches.extend(
                    value for value in data.values() if isinstance(value, dict)
                )
                for patch in patches:
                    messages = patch.get("messages")
                    if not isinstance(messages, list):
                        continue
                    for msg in messages:
                        if isinstance(msg, dict) and msg.get("role") == "assistant":
                            content = msg.get("content", "")
                            if content:
                                yield content

    async def get_chat_interrupt(self, thread_id: str) -> dict[str, Any] | None:
        """Return the pending interrupt payload for *thread_id*, or ``None``."""
        state = await self._client.threads.get_state(thread_id)
        nxt = list(state.get("next") or [])
        if not nxt:
            return None
        payload = _extract_interrupt_payload(state)
        return {"tag": nxt[0], "values": payload}

    async def resume_chat(
        self,
        thread_id: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[str | AgentProgress]:
        """Resume a paused chat thread and yield any follow-up messages."""
        async for chunk in self._stream_chat_with_input(
            thread_id, command={"resume": payload}
        ):
            yield chunk

    async def send_context(self, thread_id: str, content: str) -> None:
        """Append a system-context message to a chat thread."""
        input_data = {"messages": [{"role": "system", "content": content}]}
        async for mode, data in stream_run(
            self._client,
            thread_id,
            self._chat_graph_id,
            input=input_data,
        ):
            if mode == "error":
                raise ServerError(None, str(data))

    async def get_chat_history(self, thread_id: str) -> list[dict[str, Any]]:
        """Fetch the message history from a chat thread."""
        values, _ = await get_state_values(self._client, thread_id)
        return values.get("messages") or []

    async def delete_chat(self, thread_id: str) -> None:
        """Delete a chat thread and all its history."""
        await self._client.threads.delete(thread_id)

    async def rename_chat(self, thread_id: str, name: str) -> None:
        """Update a chat thread's display name."""
        await self._client.threads.update(
            thread_id, metadata={MONET_CHAT_NAME_KEY: name}
        )

    async def get_chat_name(self, thread_id: str) -> str:
        """Return the current display name for a chat thread, or empty string."""
        thread = await self._client.threads.get(thread_id)
        meta = thread.get("metadata") or {}
        return str(meta.get(MONET_CHAT_NAME_KEY, ""))

    async def list_thread_runs(
        self, thread_id: str, *, limit: int = 50
    ) -> list[ThreadRun]:
        """List LangGraph runs for *thread_id* with interrupt→resume links.

        Sorts by created_at ascending. When a run has status ``interrupted``,
        the next non-interrupted run is linked via ``resumed_by``.
        """
        raw = await self._client.runs.list(thread_id, limit=limit)
        raw.sort(key=lambda r: str(r["created_at"]))
        runs: list[ThreadRun] = []
        pending_interrupted: str = ""
        for r in raw:
            rid = str(r["run_id"])
            status = str(r["status"])
            created = str(r["created_at"])
            if pending_interrupted and status != "interrupted":
                runs = [
                    ThreadRun(
                        run_id=p.run_id,
                        status=p.status,
                        created_at=p.created_at,
                        resumed_by=rid
                        if p.run_id == pending_interrupted
                        else p.resumed_by,
                    )
                    for p in runs
                ]
                pending_interrupted = ""
            if status == "interrupted":
                pending_interrupted = rid
            runs.append(ThreadRun(run_id=rid, status=status, created_at=created))
        return runs

    async def count_thread_runs(self, thread_id: str) -> int:
        """Return the number of LangGraph runs on *thread_id*."""
        raw = await self._client.runs.list(thread_id, limit=100)
        return len(raw)

    async def get_most_recent_chat(self) -> str | None:
        """Return the thread_id of the most recently active chat."""
        threads = await self._client.threads.search(
            metadata={MONET_GRAPH_KEY: self._chat_graph_id},
            limit=1,
            sort_by="updated_at",
            sort_order="desc",
        )
        if not threads:
            return None
        return str(threads[0]["thread_id"])

    async def get_thread_transcript(self, thread_id: str) -> list[dict[str, Any]]:
        """Fetch the unified, chronological transcript for a chat thread."""
        import httpx

        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        url = self._url.rstrip("/") + f"/api/v1/threads/{thread_id}/transcript"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return list(data.get("entries", []))
