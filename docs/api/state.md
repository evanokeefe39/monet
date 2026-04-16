# RunState + Form-schema interrupts

This page documents two contracts that drive every monet pipeline
post-Track-B: the public ``RunState`` schema for compound graphs, and
the form-schema convention for interrupts.

## RunState

``RunState`` (``monet.orchestration.RunState``) is the parent state
schema for ``build_default_graph`` — the compound graph that composes
``planning`` and ``execution`` subgraphs as nodes.

```python
class RunState(TypedDict, total=False):
    task: str
    run_id: str
    trace_id: str
    work_brief_pointer: ArtifactPointer | None
    routing_skeleton: dict[str, Any] | None
    plan_approved: bool | None
    wave_results: Annotated[list[dict[str, Any]], _append_reducer]
    wave_reflections: Annotated[list[dict[str, Any]], _append_reducer]
    abort_reason: str | None
```

All fields are optional. Subgraphs read and write the keys they need;
LangGraph maps shared keys between the parent and each subgraph by
name. Subgraph-private fields (e.g. ``revision_count``,
``planning_context``, ``trace_carrier``) stay inside the subgraph's
schema and do not appear on ``RunState``.

### Versioning policy

The package version is the contract version.

- **Minor** (``0.X.0`` → ``0.X+1.0``): additive — new optional fields
  may be added. Existing extensions continue to compile.
- **Major** (``0.X.0`` → ``1.0.0`` or beyond): breaking — fields may
  be removed or renamed. Extensions must be updated.

There is no embedded ``state_version`` field; consumers track via
``monet.__version__`` if they need to branch behaviour.

### Extension pattern

User-defined graphs that compose monet's subgraphs alongside their own
nodes extend ``RunState`` via ``TypedDict`` inheritance:

```python
from monet.orchestration import (
    RunState,
    build_planning_subgraph,
    build_execution_subgraph,
)
from langgraph.graph import END, START, StateGraph


class MyRunState(RunState, total=False):
    review_score: float | None
    review_notes: list[str]


async def review_node(state: MyRunState) -> dict[str, Any]:
    score = score_my_outputs(state.get("wave_results", []))
    return {
        "review_score": score,
        "review_notes": [f"reviewed at score={score}"],
    }


def build_reviewed_default() -> StateGraph[MyRunState]:
    g = StateGraph(MyRunState)
    g.add_node("planning",  build_planning_subgraph().compile())
    g.add_node("execution", build_execution_subgraph().compile())
    g.add_node("review",    review_node)
    g.add_edge(START, "planning")
    g.add_edge("planning", "execution")
    g.add_edge("execution", "review")
    g.add_edge("review", END)
    return g
```

Register the resulting graph in ``aegra.json`` and declare an
``[entrypoints.<name>]`` block in ``monet.toml`` to drive it from
``monet run``. See ``examples/`` for a full custom-pipeline example.

## Form-schema interrupts

Any LangGraph node that calls ``interrupt(value)`` produces a generic
``Interrupt(tag, values, next_nodes)`` event on the client side.
Consumers cannot know in advance what shape the value dict carries,
so monet ships a soft convention: emit a form schema, and any
consumer can render and resume uniformly.

### Envelope

```python
from monet.client import Form, Field

form: Form = {
    "prompt": "Approve this plan?",
    "fields": [
        {
            "name": "action",
            "type": "radio",
            "label": "Decision",
            "options": [
                {"value": "approve", "label": "Approve"},
                {"value": "revise",  "label": "Request changes"},
                {"value": "reject",  "label": "Reject"},
            ],
        },
        {
            "name": "feedback",
            "type": "textarea",
            "label": "Feedback (for revise)",
            "required": False,
        },
    ],
    "context": {
        "routing_skeleton": ...,
    },
}
decision = interrupt(form)   # `Form` is a TypedDict; dict literals also work
```

The ``Form`` and ``Field`` TypedDicts are opt-in type hints — graphs
that prefer plain dict literals work identically. There is no runtime
validation of the envelope; renderers degrade gracefully when ``fields``
is absent.

### Field-type vocabulary

| ``type``   | Required keys                | CLI rendering            | Resume value     |
|------------|------------------------------|--------------------------|------------------|
| ``text``     | ``name``                       | one-line ``click.prompt``  | ``str``          |
| ``textarea`` | ``name``                       | one-line ``click.prompt``  | ``str``          |
| ``int``      | ``name``                       | numeric prompt           | ``int``          |
| ``bool``     | ``name``                       | ``click.confirm``          | ``bool``         |
| ``radio``    | ``name``, ``options[]``          | numbered list, pick one  | ``str``          |
| ``select``   | ``name``, ``options[]``          | numbered list, pick one  | ``str``          |
| ``checkbox`` | ``name``, ``options[]``          | comma-separated indices  | ``list[str]``    |
| ``hidden``   | ``name``, ``value``              | not rendered             | pass-through     |

Each ``options[]`` item is ``{"value": str, "label": str}``.
Optional keys on every field: ``label``, ``default``, ``required``
(default ``True``), ``help``.

### Resume

The CLI reads the form, prompts per-field, and posts a payload keyed
by ``field.name``:

```python
async for event in client.run("default", task_input("topic", "")):
    if isinstance(event, Interrupt):
        payload = render_interrupt_form(event.values)
        await client.resume(event.run_id, event.tag, payload)
```

Programmatic consumers skip the renderer and build the payload
directly:

```python
await client.resume(run_id, tag, {"action": "approve"})
await client.resume(run_id, tag, {"action": "revise", "feedback": "be shorter"})
```

### Interrupt tag semantics

After a subgraph-internal ``interrupt()``, the parent's pending tag
is the **parent node name** (e.g. ``"planning"`` /
``"execution"``), not the subgraph's interrupt-node name. Consumers
that read ``event.tag`` from the streamed Interrupt observe this
correctly; hardcoded tag strings from the prior typed-event API
(e.g. ``"human_approval"``) no longer match.
