"""Pin :meth:`MonetClient._await_interrupted_status` behavior.

Covers the I6 resume/stream race: Aegra rejects resume until
``thread.status == "interrupted"``, but the graph checkpointer exposes
``next`` before ``finalize_run`` commits that status. ``MonetClient``
polls the thread row so callers see a deterministic timeout instead of
a 400 race.
"""

from __future__ import annotations

import pytest

from monet.client import MonetClient, MonetClientError


class _StubThreads:
    def __init__(self, statuses: list[str]) -> None:
        self._statuses = list(statuses)
        self.calls = 0

    async def get(self, thread_id: str) -> dict[str, str]:
        self.calls += 1
        idx = min(self.calls - 1, len(self._statuses) - 1)
        return {"thread_id": thread_id, "status": self._statuses[idx]}


class _StubClient:
    def __init__(self, statuses: list[str]) -> None:
        self.threads = _StubThreads(statuses)


def _make_client(stub: _StubClient) -> MonetClient:
    client = MonetClient.__new__(MonetClient)
    client._client = stub  # type: ignore[assignment]
    from monet.client._core import _ClientCore
    from monet.client._run import RunClient

    client._core = _ClientCore(
        url="",
        api_key=None,
        data_url="",
        client=stub,
        store=None,
        entrypoints={},
        graph_roles={},
    )  # type: ignore
    client._runs = RunClient(client._core)
    return client


async def test_await_returns_when_status_already_interrupted() -> None:
    stub = _StubClient(["interrupted"])
    client = _make_client(stub)

    await client._runs._await_interrupted_status("t-1", timeout=0.5)

    assert stub.threads.calls == 1


async def test_await_polls_until_status_flips() -> None:
    stub = _StubClient(["busy", "busy", "interrupted"])
    client = _make_client(stub)

    await client._runs._await_interrupted_status("t-1", timeout=1.0, interval=0.01)

    assert stub.threads.calls == 3


async def test_await_raises_on_timeout() -> None:
    stub = _StubClient(["busy"])
    client = _make_client(stub)

    with pytest.raises(MonetClientError, match="did not reach 'interrupted'"):
        await client._runs._await_interrupted_status("t-1", timeout=0.1, interval=0.01)
