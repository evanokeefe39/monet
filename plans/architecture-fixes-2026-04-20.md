# Architecture Fixes Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Fix four architectural issues identified in the queue/server/cli-worker/client stack: (1) duplicate interrupt-extraction helpers in client module, (2) duplicate agent-import logic in CLI worker, (3) missing `CancelledError` guard in local worker mode, (4) verify `subscribe_progress` call sites handle `NotImplementedError`.

**Architecture:** Extract shared helpers to a private `_helpers.py` module in each package boundary. Add missing error guards. Add call-site verification with tests.

**Tech Stack:** Python 3.12+, pytest, pytest-asyncio, ruff, mypy

---

## Task 1: Extract duplicate `_extract_interrupt_payload` to `client/_helpers.py`

**Objective:** Remove duplicated interrupt-extraction helper from both `client/__init__.py` and `client/chat.py`, placing it in a shared module.

**Files:**
- Create: `src/monet/client/_helpers.py`
- Modify: `src/monet/client/__init__.py:75-102` (remove), `src/monet/client/chat.py:33-53` (remove)

**Step 1: Create `src/monet/client/_helpers.py` with the extracted function**

```python
"""Private helpers shared across client submodules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


def extract_interrupt_payload(state: Any) -> dict[str, Any]:
    """Pull the first interrupt payload off a LangGraph state snapshot.

    The payload lives on ``state.tasks[0].interrupts[0].value`` in the
    LangGraph SDK response; it is not mirrored into
    ``state.values["__interrupt__"]``. Tolerates both mapping-style and
    attribute-style access because the SDK returns plain dicts for some
    endpoints and pydantic-esque objects for others.
    """

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
```

**Step 2: Update `src/monet/client/__init__.py`**

Remove the `_extract_interrupt_payload` function (lines ~75-102). Add import at the top of the file:
```python
from monet.client._helpers import extract_interrupt_payload as _extract_interrupt_payload
```

Update the call site in `MonetClient.run()` at approximately line 285:
```python
# Before
values, nxt = await get_state_values(self._client, thread)
if nxt:
    payload = _extract_interrupt_payload(state)

# After — uses the imported alias with the original internal name
values, nxt = await get_state_values(self._client, thread)
if nxt:
    payload = _extract_interrupt_payload(state)
```

**Step 3: Update `src/monet/client/chat.py`**

Remove the `_extract_interrupt_payload` function (lines ~33-53). Add import:
```python
from monet.client._helpers import extract_interrupt_payload as _extract_interrupt_payload
```

Update the call site in `ChatClient.get_chat_interrupt()` at approximately line 181:
```python
payload = _extract_interrupt_payload(state)
```

**Step 4: Verify no type errors**

Run: `cd /mnt/c/Users/evano/repos/monet && uv run mypy src/monet/client/`
Expected: zero errors

**Step 5: Verify lint**

Run: `cd /mnt/c/Users/evano/repos/monet && uv run ruff check src/monet/client/`
Expected: clean

**Step 6: Run client tests**

Run: `cd /mnt/c/Users/evano/repos/monet && uv run pytest tests/ -k client -v --tb=short`
Expected: all client tests pass

**Step 7: Commit**

```bash
git add src/monet/client/_helpers.py src/monet/client/__init__.py src/monet/client/chat.py
git commit -m "refactor(client): extract _extract_interrupt_payload to shared _helpers module"
```

---

## Task 2: Extract duplicate `_build_agent_progress` to `client/_helpers.py`

**Objective:** Remove the second duplicated helper, `_build_agent_progress`, from both client files.

**Files:**
- Modify: `src/monet/client/_helpers.py` (add function), `src/monet/client/__init__.py` (remove), `src/monet/client/chat.py` (remove)

**Step 1: Add `_build_agent_progress` to `src/monet/client/_helpers.py`**

Add after `extract_interrupt_payload`:

```python
def build_agent_progress(run_id: str, data: dict[str, Any]) -> "AgentProgress | None":
    """Convert a custom-stream wire dict into an :class:`AgentProgress`.

    Returns ``None`` when the dict does not carry an ``agent`` field —
    callers should skip such payloads rather than emit a malformed event.
    """
    from monet.client._events import AgentProgress

    agent = data.get("agent", "")
    if not agent:
        return None
    return AgentProgress(
        run_id=run_id,
        agent_id=agent,
        status=data.get("status", ""),
        reasons=data.get("reasons", ""),
    )
```

Note the import is inside the function to avoid a circular import (AgentProgress is in `_events.py` which imports from `_wire.py`).

**Step 2: Update `src/monet/client/__init__.py`**

Remove `_build_agent_progress` function (lines ~135-149). Replace the internal definition with:
```python
from monet.client._helpers import (
    build_agent_progress as _build_agent_progress,
)
```

Update call site (inside `stream_run` loop, ~line 273):
```python
# Already named the same, just remove the local definition
```

**Step 3: Update `src/monet/client/chat.py`**

Remove `_build_agent_progress` function (lines ~56-66). Replace with import:
```python
from monet.client._helpers import build_agent_progress as _build_agent_progress
```

**Step 4: Verify**

```bash
cd /mnt/c/Users/evano/repos/monet && uv run mypy src/monet/client/ && uv run ruff check src/monet/client/ && uv run pytest tests/ -k client -v --tb=short
```

Expected: zero mypy errors, zero ruff errors, all client tests pass

**Step 5: Commit**

```bash
git add src/monet/client/_helpers.py src/monet/client/__init__.py src/monet/client/chat.py
git commit -m "refactor(client): extract _build_agent_progress to shared _helpers module"
```

---

## Task 3: Extract shared `_import_agents` helper in `cli/_worker.py`

**Objective:** Consolidate the duplicate agent-discovery-and-import logic into a single `_discover_and_import_agents` helper used by both `_run_worker` and `_run_push`.

**Files:**
- Modify: `src/monet/cli/_worker.py:172-221` (consolidate)

**Step 1: Read the current state of `_worker.py` to confirm line positions**

Run: `cat -n src/monet/cli/_worker.py | sed -n '170,230p'`

**Step 2: Write the consolidated helper above `_run_push`**

Insert this new function at approximately line 141 (before `_run_push`):

```python
def _discover_and_import_agents(path: Path, cfg: WorkerConfig) -> list["DiscoveredAgent"]:
    """Discover + import agents from *path* and load declarative agents.toml.

    Used by both push-mode and pull-mode worker entrypoints so both paths
    exercise identical discovery logic.
    """
    from monet.cli._discovery import discover_agents

    discovered = discover_agents(path)
    logger.info("Discovered %d agent(s) in %s", len(discovered), path)

    # Deduplicate files to avoid double-importing.
    discovered_files: list[Path] = list(dict.fromkeys(a.file for a in discovered))

    for agent_file in discovered_files:
        spec = importlib.util.spec_from_file_location(agent_file.stem, agent_file)
        if spec is None or spec.loader is None:
            logger.warning("Could not load %s, skipping", agent_file)
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[agent_file.stem] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        logger.info("Imported %s", agent_file)

    if cfg.agents_toml is not None:
        from monet.core._agents_config import load_agents

        count = load_agents(cfg.agents_toml)
        logger.info("Registered %d agent(s) from %s", count, cfg.agents_toml)

    return discovered
```

**Step 3: Update `_run_push`**

Replace the `_import_agents(path, cfg)` call inside `_run_push` (around line 159) with:
```python
_discover_and_import_agents(path, cfg)
```

**Step 4: Update `_run_worker`**

Replace the inline discovery + import block (lines 195-221) with:
```python
discovered = _discover_and_import_agents(path, cfg)
```

**Step 5: Delete the old `_import_agents` function**

Remove the standalone `_import_agents` function (formerly lines 172-192).

**Step 6: Verify**

```bash
cd /mnt/c/Users/evano/repos/monet && uv run mypy src/monet/cli/_worker.py && uv run ruff check src/monet/cli/_worker.py
```

Expected: zero errors

**Step 7: Run worker tests**

Run: `cd /mnt/c/Users/evano/repos/monet && uv run pytest tests/ -k worker -v --tb=short`
Expected: all worker tests pass

**Step 8: Commit**

```bash
git add src/monet/cli/_worker.py
git commit -m "refactor(cli): extract _discover_and_import_agents to eliminate duplicate agent-import logic"
```

---

## Task 4: Add `CancelledError` guard to `_run_local`

**Objective:** Make `_run_local` handle `asyncio.CancelledError` the same way `_run_remote` does, ensuring graceful shutdown when the worker's task is cancelled from the outside.

**Files:**
- Modify: `src/monet/cli/_worker.py` (around line 303)

**Step 1: Read current `_run_local`**

Run: `cat -n src/monet/cli/_worker.py | sed -n '303,322p'`

**Step 2: Update `_run_local`**

Replace:
```python
async def _run_local(cfg: WorkerConfig) -> None:
    """Run worker in local mode with an in-memory queue."""
    from monet.orchestration._invoke import configure_queue
    from monet.queue import InMemoryTaskQueue, run_worker

    queue = InMemoryTaskQueue()
    configure_queue(queue)
    logger.info("Worker running in local mode (pool=%s)", cfg.pool)

    try:
        await run_worker(
            queue,
            pool=cfg.pool,
            max_concurrency=cfg.concurrency,
            poll_interval=cfg.poll_interval,
            shutdown_timeout=cfg.shutdown_timeout,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Worker shutting down")
```

**Step 3: Verify**

```bash
cd /mnt/c/Users/evano/repos/monet && uv run mypy src/monet/cli/_worker.py && uv run ruff check src/monet/cli/_worker.py
```

Expected: zero errors

**Step 4: Run worker tests**

Run: `cd /mnt/c/Users/evano/repos/monet && uv run pytest tests/ -k worker -v --tb=short`
Expected: all worker tests pass

**Step 5: Commit**

```bash
git add src/monet/cli/_worker.py
git commit -m "fix(cli): add CancelledError guard to _run_local for graceful shutdown"
```

---

## Task 5: Verify `subscribe_progress` call sites handle `NotImplementedError`

**Objective:** Confirm all call sites of `subscribe_progress` either guard against `NotImplementedError` or dispatch based on queue backend type.

**Files:**
- Read: `src/monet/orchestration/_invoke.py`

**Step 1: Find all `subscribe_progress` call sites**

Run: `grep -n "subscribe_progress" src/monet/`

**Step 2: Read `_invoke.py` to understand how backends are dispatched**

Run: `grep -n "subscribe_progress\|isinstance\|InMemoryTaskQueue\|RedisStreams\|RemoteQueue" src/monet/orchestration/_invoke.py | head -40`

**Step 3: Add test that `RemoteQueue.subscribe_progress` raises `NotImplementedError`**

Create: `tests/unit/core/test_worker_client.py`

```python
import pytest
from monet.core.worker_client import RemoteQueue


def test_remote_queue_subscribe_progress_raises_not_implemented():
    """RemoteQueue.subscribe_progress intentionally raises NotImplementedError.

    Progress flows from worker to server via POST /tasks/{id}/progress.
    Subscribing for progress server-side is not applicable.
    """
    client = ...  # build a minimal mock WorkerClient
    queue = RemoteQueue(client, pool="test")
    with pytest.raises(NotImplementedError, match="subscribe_progress is not supported"):
        queue.subscribe_progress("some-task-id")
```

**Step 4: Verify all callers are already safe**

If call sites already guard with `isinstance` checks or try/except, add a docstring comment and close this task. If any call site is unprotected, add the guard.

**Step 5: Commit**

```bash
git add tests/unit/core/test_worker_client.py
git add src/monet/orchestration/_invoke.py  # if modified
git commit -m "test(core): verify RemoteQueue.subscribe_progress raises NotImplementedError"
```

---

## Task 6: Full verification — run the full test suite

**Objective:** Ensure all changes pass the full CI suite.

**Step 1: Run ruff and mypy on entire src/**

```bash
cd /mnt/c/Users/evano/repos/monet && uv run ruff check src/ && uv run mypy src/
```

Expected: zero errors

**Step 2: Run full test suite**

```bash
cd /mnt/c/Users/evano/repos/monet && uv run pytest tests/ -x -q --tb=short
```

Expected: all tests pass

**Step 3: Run from clean state (no cache)**

```bash
cd /mnt/c/Users/evano/repos/monet && uv run pytest tests/ -x -q --tb=short --cache-clear
```

Expected: all tests pass

---

## Verification Checklist

After all tasks:

- [ ] `src/monet/client/_helpers.py` exists with `extract_interrupt_payload` and `build_agent_progress`
- [ ] `_extract_interrupt_payload` removed from `client/__init__.py` and `client/chat.py`
- [ ] `_build_agent_progress` removed from `client/__init__.py` and `client/chat.py`
- [ ] `_discover_and_import_agents` helper exists in `_worker.py`
- [ ] `_import_agents` function removed from `_worker.py`
- [ ] `_run_local` has `try/except (KeyboardInterrupt, asyncio.CancelledError)` guard
- [ ] `RemoteQueue.subscribe_progress` test exists and passes
- [ ] `uv run mypy src/` → zero errors
- [ ] `uv run ruff check src/` → zero errors
- [ ] `uv run pytest tests/` → all pass
