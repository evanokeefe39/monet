"""Python headless driver: emits the same JSONL vocabulary as Go's scenario.go.

Shared-shape output is what lets tests/compat/run.py diff the two client
implementations. Each record is one JSON object:

    {"kind": "...", "payload": ..., "step": int, "meta": "..."}

Mirrors go/cmd/monet-tui/scenario.go. Keep the event vocabulary in lock-step
with that file — normalize.py should not need to paper over naming drift.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, TextIO

from monet.client._wire import (
    MONET_CHAT_NAME_KEY,
    MONET_GRAPH_KEY,
    create_thread,
    get_state_values,
    make_client,
    stream_run,
)


def emit(
    sink: TextIO,
    kind: str,
    *,
    step: int,
    payload: Any = None,
    meta: str = "",
) -> None:
    rec: dict[str, Any] = {"kind": kind, "step": step}
    if payload is not None:
        rec["payload"] = payload
    if meta:
        rec["meta"] = meta
    sink.write(json.dumps(rec) + "\n")
    sink.flush()


async def _drive_stream(
    client: Any,
    thread_id: str,
    graph_id: str,
    *,
    input: dict[str, Any] | None,
    command: dict[str, Any] | None,
    sink: TextIO,
    step: int,
) -> None:
    started_emitted = False
    async for mode, data in stream_run(
        client, thread_id, graph_id, input=input, command=command
    ):
        if mode == "error":
            emit(
                sink,
                "run_failed",
                step=step,
                payload={"error": str(data)},
            )
            return
        if not isinstance(data, dict):
            continue
        if mode == "updates":
            if "__interrupt__" in data:
                emit(
                    sink,
                    "interrupt",
                    step=step,
                    payload={"values": data.get("__interrupt__")},
                )
                # Keep draining — closing the stream here cancels Aegra's
                # finalize_run before it commits thread.status="interrupted",
                # and the next resume step 400s. Waiting for natural close
                # is faster and more reliable than polling afterward.
                continue
            run_id = data.get("run_id")
            if run_id and not started_emitted:
                emit(
                    sink,
                    "run_started",
                    step=step,
                    payload={"run_id": run_id},
                )
                started_emitted = True
                continue
            emit(sink, "updates", step=step, payload=data)
        elif mode == "custom":
            status = data.get("status")
            if status:
                emit(sink, "progress", step=step, payload=data)
                continue
            if data.get("signal_type"):
                emit(sink, "signal", step=step, payload=data)
                continue
            emit(sink, "updates", step=step, payload=data)

    # SSE closed — synthesize terminal event from state.
    values, nxt = await get_state_values(client, thread_id)
    if nxt:
        emit(
            sink,
            "interrupt",
            step=step,
            payload={
                "tag": nxt[0],
                "values": values.get("__interrupt__"),
            },
        )
    else:
        emit(sink, "run_complete", step=step)


async def run_scenario(
    doc: dict[str, Any],
    *,
    server_url: str,
    api_key: str,
    sink: TextIO,
) -> None:
    client = make_client(server_url, api_key=api_key)
    graph_id = doc.get("graph") or "chat"
    threads: dict[str, str] = {}

    for i, step in enumerate(doc.get("steps") or []):
        op = step.get("op")
        if op == "create_thread":
            name = step.get("name") or f"t{len(threads)}"
            meta: dict[str, Any] = {MONET_GRAPH_KEY: graph_id}
            if name:
                meta[MONET_CHAT_NAME_KEY] = name
            thread_id = await create_thread(client, metadata=meta)
            threads[name] = thread_id
            emit(sink, "thread_created", step=i, meta=name, payload=thread_id)
        elif op == "send":
            tid = _resolve(threads, step.get("thread"))
            message = step.get("message", "")
            await _drive_stream(
                client,
                tid,
                graph_id,
                input={"messages": [{"role": "user", "content": message}]},
                command=None,
                sink=sink,
                step=i,
            )
        elif op == "resume":
            tid = _resolve(threads, step.get("thread"))
            # Poll briefly for ThreadORM.status to commit to "interrupted"
            # — the state endpoint exposes next-nodes the moment the graph
            # hits interrupt(), but the server's resume validator rejects
            # until the status row is written. Same wait MonetClient.resume
            # applies; harness mirrors it to keep py_headless self-contained.
            await _wait_interrupted(client, tid)
            await _drive_stream(
                client,
                tid,
                graph_id,
                input=None,
                command={
                    "resume": {
                        "tag": step.get("tag", ""),
                        "payload": step.get("payload") or {},
                    }
                },
                sink=sink,
                step=i,
            )
        elif op == "get_state":
            tid = _resolve(threads, step.get("thread"))
            values, nxt = await get_state_values(client, tid)
            emit(sink, "state", step=i, payload={"values": values, "next": nxt})
        else:
            emit(sink, "scenario_error", step=i, payload=f"unknown op {op!r}")
            return

    emit(sink, "scenario_end", step=-1, meta=str(doc.get("name") or ""))


async def _wait_interrupted(client: Any, thread_id: str, timeout: float = 10.0) -> None:
    """Poll thread.status until 'interrupted' — the resume validator
    rejects until the DB row is committed. next-nodes alone are not
    enough (they're exposed by the checkpointer ahead of the commit)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        thread = await client.threads.get(thread_id)
        if thread.get("status") == "interrupted":
            return
        await asyncio.sleep(0.1)
    raise RuntimeError(
        f"thread {thread_id} did not reach 'interrupted' status within {timeout}s"
    )


def _resolve(threads: dict[str, str], key: str | None) -> str:
    if not key:
        if not threads:
            raise RuntimeError("no thread created yet")
        return next(iter(threads.values()))
    if key not in threads:
        raise RuntimeError(f"unknown thread ref {key!r}")
    return threads[key]


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="tests.compat.py_headless")
    ap.add_argument("--scenario", required=True, type=Path)
    ap.add_argument("--url", required=True)
    ap.add_argument("--api-key", default="")
    args = ap.parse_args()

    doc = json.loads(args.scenario.read_text())
    asyncio.run(
        run_scenario(
            doc,
            server_url=args.url,
            api_key=args.api_key,
            sink=sys.stdout,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
