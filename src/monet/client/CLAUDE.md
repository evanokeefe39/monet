# monet.client — MonetClient

## Responsibility

Graph-agnostic client for a monet LangGraph server. No pipeline-specific verbs. Domain semantics belong in caller layer.

## Public interface

```python
class MonetClient:
    async def run(graph_id, input, run_id?) -> AsyncIterator[CoreEvent]
    async def resume(run_id, tag, payload) -> None
    async def abort(run_id) -> None
    async def invoke_agent(agent_id, command, input) -> dict
    async def list_capabilities() -> list[Capability]
    async def slash_commands() -> list[str]
    async def list_runs(limit?) -> list[RunSummary]
    async def get_run(run_id) -> RunDetail
    async def list_pending() -> list[PendingDecision]
    async def list_graphs() -> list[str]
    chat: ChatClient   # chat-specific ops via monet.client.chat
```

## Event stream

`run()` yields: `RunStarted` → `NodeUpdate | AgentProgress | SignalEmitted` → `Interrupt | RunComplete | RunFailed`.

Interrupts surface as generic `Interrupt` event, answered via `resume(run_id, tag, payload)`.

## HITL

`resume()` validates run is paused at `tag` before dispatching. Raises `RunNotInterrupted`, `AlreadyResolved`, `AmbiguousInterrupt`, `InterruptTagMismatch` on mismatch.

`abort()` resumes with `{"resume": {"action": "abort"}}` — graphs not consuming that shape simply continue.

## Invocable graphs

`run(graph_id, ...)` requires `graph_id` declared in `monet.toml [entrypoints]`. Raises `GraphNotInvocable` if not declared.

## What client does NOT own

- Graph topology or routing
- Queue implementation
- Config loading (caller passes `ClientConfig`)
