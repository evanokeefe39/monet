# Lessons

Session-level patterns captured after corrections, regressions, or
surprises. Reviewed at session start so the same mistake is not made
twice. Ordered newest first.

## 2026-04-08 — BlockingError regression from Path.resolve() on Windows

**Trigger:** commit `c735f8f` ("Use Path.as_uri() for filesystem artifact
URLs") added `.resolve()` to the URL assembly in
`src/monet/artifacts/_storage.py::FilesystemStorage.write`. Under
`langgraph dev`, `blockbuster` intercepts blocking syscalls on the
event loop and raises `BlockingError`. Every `researcher/writer/
publisher` invocation crashed at the URL line, was caught by the
`@agent` wrapper's `except` branch, and turned into an empty
`AgentResult(success=False, output="", artifacts=[])`. QA rationalised
the empty content into three "no content provided" fail verdicts and
the execution graph aborted at the revision limit with 0 phases
complete. The user ran the example twice and saw only blank wave
results. Root cause was found in commit `9638ecd` after a Phase A
diagnostic instrumentation pass.

**Five whys:**

1. Why were wave results empty? `@agent` wrapper caught a `BlockingError`
   and returned `success=False, output=""`.
2. Why did it catch a `BlockingError`? `FilesystemStorage.write` called
   `Path.resolve()` on the artifact file path.
3. Why was `.resolve()` being called? Commit `c735f8f` added it under
   the mistaken assumption that `as_uri()` needs an absolute path that
   only `.resolve()` can guarantee. In fact `self.root` is already
   absolute (built from `Path(__file__).resolve().parent` at import
   time in `server_graphs.py`, off the event loop), so
   `(artifact_dir / "content").as_uri()` works without `.resolve()`.
4. Why was the blocking call not caught earlier? No test exercised
   `FilesystemStorage.write` under an ASGI event loop with
   `blockbuster` installed. Unit tests run in plain asyncio where
   `blockbuster` is not active.
5. Why did the failure not surface loudly in the CLI? The execution
   pipeline had no andon cord: `agent_node` did not inspect
   `result.success`, `display.print_wave_results` did not render
   `signals`, and `_wrap_result` had no guard for the "success=True
   with empty output and zero artifacts" contradiction that silently
   ships garbage downstream.

**Fixes landed:**

- `9638ecd` — drop `.resolve()`, keep `.as_uri()`; comment explains
  the Windows blocking-call trap.
- `b996ee9` — `_wrap_result` treats empty-string/None return + no
  artifacts as a defect, downgrades to `success=False` with a
  `semantic_error:empty_agent_result` signal. Opt out via
  `@agent(..., allow_empty=True)`.
- `d415c08` — `agent_node` emits an `agent failed` progress event;
  `display.print_streaming_event` renders it inline; `print_wave_results`
  renders a `!!` block for wave_results with failure signals;
  `print_summary` counts failed invocations.
- `b5f9fd9` — W3C traceparent propagation via LangGraph state so all
  agent spans in one execution run share a trace_id in Langfuse.

## Patterns to apply going forward

- **Filesystem code inside async agents must be ASGI-event-loop-safe.**
  Never call `Path.resolve()`, `Path.exists()`, `os.getcwd()`, or any
  sync I/O on a path that might be evaluated on the event loop. Resolve
  paths at import time in the host process (`server_graphs.py`,
  `app.py`), store the absolute form, and only do pure string
  manipulation inside agent execution. Use `aiofiles` or
  `asyncio.to_thread` for unavoidable blocking I/O. Reviewer check:
  grep for `\.resolve\(|os\.getcwd|os\.path\.realpath` under
  `src/monet/artifacts/` and `src/monet/agents/`.

- **An agent that returns "" and writes no artifacts is a defect, not
  a success.** The `_wrap_result` poka-yoke catches this now, but
  reviewers should still flag any agent implementation whose happy
  path depends on an empty return value. If the agent is legitimately
  signal-only (ack handlers, routers), it must declare
  `@agent(..., allow_empty=True)` explicitly so the intent is visible
  in the decorator signature.

- **Before writing a specification for filesystem code, add a
  behavioural contract: "GIVEN an ASGI event loop with blockbuster
  active, WHEN the function is called, THEN it completes without
  raising BlockingError."** This is a schema gap in the Executable
  Specification format. Without an explicit contract, reviewers will
  not notice the gap and unit tests will silently pass in plain
  asyncio.

- **Observability gaps that let a regression burn three wave retries
  undetected cost more than the bug itself.** When a defect reaches
  the user without naming itself, the first fix should be the
  surfacing, not the bug. The bug-fix commit is cheap once you can
  see what's failing; the surfacing work (andon cord, poka-yoke,
  review interface) is the durable investment.

- **Disk state lies when you assume it was written by the run you're
  debugging.** The first diagnostic pass read three researcher
  artifacts with `run_id: "89a79f82"` off disk and incorrectly
  concluded "the write succeeded, the bug must be in the collector
  handoff." They were actually from an earlier run that happened to
  have the same short run_id. Always cross-check timestamps against
  the run start time before drawing conclusions from persistent
  state.
