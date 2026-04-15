# ADR 001 — Collapse the three-graph supervisor split

Status: Accepted (Track B, 2026-04-15)

## Context

Through the prior client-decoupling refactor, monet shipped the default
pipeline as three independent graphs — ``entry``, ``planning``, and
``execution`` — composed across three threads by an adapter
(``monet.pipelines.default.adapter``). Each graph was registered
separately in ``aegra.json`` and ran on its own LangGraph thread with
its own checkpointer. A typed event projection layer
(``PlanInterrupt`` / ``ExecutionInterrupt`` / ``WaveComplete`` / etc.)
mapped raw stream chunks to typed events; typed HITL verbs
(``approve_plan`` / ``revise_plan`` / ``reject_plan`` /
``retry_wave`` / ``abort_run``) wrapped ``client.resume`` for callers.

Two concrete problems surfaced:

1. **Resume bug** — ``cli/_run.py:_resume_pipeline`` was a stub. After
   the user approved a plan, the adapter's ``run()`` async generator
   had already returned (it yielded ``PlanInterrupt`` and exited), so
   nothing drove the execution thread. The auto-approve path worked
   only because it inlined the resume + ``_drive_execution`` call;
   the manual path silently exited.
2. **Coupling** — ``cli/_run.py`` branched on entrypoint name
   (``is_default``) to dispatch to the adapter's ``run()`` rather
   than the generic ``client.run()`` path. A second pipeline could
   not be added without editing CLI code, and ~350 LoC of typed
   event projection + HITL verbs lived on top of the adapter for
   one pipeline.

## Decision

Collapse the three-graph split into one compound graph using
LangGraph's idiomatic subgraph-as-node composition:

```
build_default_graph()  →  StateGraph[RunState]
  ├── add_node("entry",     build_entry_subgraph().compile())
  ├── add_node("planning",  build_planning_subgraph().compile())
  └── add_node("execution", build_execution_subgraph().compile())
```

One thread, one checkpointer, native ``interrupt()`` /
``Command(resume=...)`` for HITL.

Delete the adapter layer entirely (``monet.pipelines.default``
subpackage, ~350 LoC). The CLI uses the generic
``client.run("default", task_input(...))`` path for every entrypoint
including the default pipeline.

Replace typed interrupt events + HITL verbs with a **form-schema
convention** (``Form`` / ``Field`` TypedDicts in
``monet.client._events``). Graphs that follow the convention emit
``interrupt({"prompt": ..., "fields": [...], "context": {...}})``;
any consumer (CLI, REPL, web UI) renders the form generically and
posts a payload back via ``client.resume(run_id, tag, payload)``.
Graphs that don't follow the convention still work — the renderer
falls back to a raw-JSON dump and a JSON resume prompt.

## Consequences

### What gets simpler

- One CLI code path (``_drive_entrypoint``) for every entrypoint.
- HITL contract is the same for built-in and user graphs: emit a
  form schema, get rendered. No verb-per-pipeline duplication.
- Resume is one LangGraph primitive (``Command(resume=...)``); the
  resume-bug stub disappears by construction.
- ``aegra.json`` declares two graphs (``default`` + ``chat``) instead
  of four.

### What gets harder

- A user who wants to run only the planning subgraph — say, to test
  it in isolation — has to compose their own ``StateGraph`` around
  ``build_planning_subgraph()`` instead of pointing at a registered
  ``planning`` graph. The subgraph composers are exported public
  API for exactly this use case.
- ``Interrupt.tag`` post-collapse reports the parent node name
  (``"planning"`` / ``"execution"``), not the subgraph-internal
  node name (``"human_approval"`` / ``"human_interrupt"``).
  Callers that hardcoded the old tag strings break; callers that
  read ``event.tag`` from the streamed Interrupt are unaffected.

### Extension pattern (OCP)

Self-hosting users extend by composing the same subgraphs under
their own parent graph:

```python
from monet.orchestration import (
    RunState,
    build_entry_subgraph,
    build_planning_subgraph,
    build_execution_subgraph,
)

class MyRunState(RunState, total=False):
    review_score: float | None

def build_reviewed_default():
    g = StateGraph(MyRunState)
    g.add_node("entry",     build_entry_subgraph().compile())
    g.add_node("planning",  build_planning_subgraph().compile())
    g.add_node("execution", build_execution_subgraph().compile())
    g.add_node("review",    my_review_node)   # user's node
    ...
    return g
```

LangGraph maps shared keys between the parent and each subgraph by
name; subgraph-private fields don't leak to the parent and parent-
only fields pass through subgraph nodes untouched. This is verified
in ``tests/test_subgraph_composition_spike.py``.

### Roadmap impact

The deferred "pluggable pipeline adapters via ``monet.toml``"
roadmap item is **retired**. Composition happens at the LangGraph
layer via Python imports; ``monet.toml [entrypoints.<name>]`` is
the registration seam, not an adapter loader. ``CLAUDE.md`` and
``docs/architecture/roadmap.md`` are updated to reflect this.

## Validation

Track B was preceded by a six-test spike file
(``tests/test_subgraph_composition_spike.py``) that pinned the
LangGraph properties this design depends on:

1. Subgraph state schemas compose under a different parent state
   schema; shared keys flow by name.
2. Parent-only fields survive subgraph node calls untouched.
3. ``MyRunState(RunState)`` + custom node + reducer round-trips.
4. ``RunState`` accepts the extension pattern at the type level.
5. ``interrupt()`` inside a subgraph pauses the parent;
   ``Command(resume=...)`` continues inside the subgraph.
6. ``stream_mode="updates" / "custom"`` with ``subgraphs=True``
   surface subgraph-internal state writes and ``get_stream_writer()``
   payloads at the parent level (so ``emit_progress`` keeps working).
7. Sequential interrupts across two subgraphs report the correct
   parent node name and resume cleanly across the cycle.

The compound graph itself is exercised by
``tests/test_default_compound_graph.py``: simple-triage short-circuit,
pause-at-planning-interrupt, approve-and-drive, and reject-halts-
pipeline. End-to-end coverage against a real ``monet dev`` server
lives behind the ``e2e`` pytest marker in ``tests/e2e/``.
