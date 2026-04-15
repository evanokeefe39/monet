# Custom pipeline — graph-level extension

Composes monet's built-in entry/planning/execution subgraphs under a
user-defined parent graph, with an extra `review` node that scores
the execution output. Demonstrates the OCP extension pattern from
Track B.

## Prerequisites

- Docker Desktop
- Python 3.12+ and `uv`
- At least one LLM key (planning + execution call the built-in
  reference agents)

## Setup

```bash
cd examples/custom-pipeline
uv sync
cp .env.example .env
```

## Run

Terminal 1:

```bash
monet dev
```

Terminal 2:

```bash
# The new "reviewed" entrypoint (declared in monet.toml) drives the
# user-defined graph end to end: entry → planning (HITL) → execution
# → review → END.
monet run "AI trends in healthcare" --graph reviewed --auto-approve

# The stock default pipeline is still reachable unchanged.
monet run "AI trends in healthcare" --auto-approve
```

## What it shows

### State schema extension

```python
# graphs/reviewed.py
class MyRunState(RunState, total=False):
    review_score: float | None
    review_notes: Annotated[list[str], _append_str]
```

`MyRunState` is a superset of monet's public `RunState`. LangGraph
maps shared keys (`task`, `triage`, `wave_results`, …) by name
between the parent and each built-in subgraph. Subgraph-private
fields never leak up; user-only fields pass through subgraph nodes
untouched.

### Subgraph composition

```python
g = StateGraph(MyRunState)
g.add_node("entry",     build_entry_subgraph().compile())
g.add_node("planning",  build_planning_subgraph().compile())
g.add_node("execution", build_execution_subgraph().compile())
g.add_node("review",    review_node)   # user node
...
```

No fork of monet, no new graph adapter — compose the subgraphs as
nodes and add your own. `review_node` reads `wave_results` (from
execution) and writes `review_score` + `review_notes` (user fields).

### Entrypoint registration

```toml
# monet.toml
[entrypoints.reviewed]
graph = "reviewed"
```

`monet run --graph reviewed <topic>` resolves via `load_entrypoints`
and streams the user graph through the generic `MonetClient.run`
path. All the same HITL / form-schema / event machinery applies —
nothing in monet knows this graph is user-defined.

## Extending further

`review_node` here is a pure Python function. Realistic review nodes
often:

- Invoke a QA agent via `invoke_agent("qa", "fast", task=...)`.
- Gate a replan loop: if `review_score < threshold`, route back to
  `planning` instead of `END`.
- Write an artifact summarising the review for downstream consumers.

The parent graph is yours to shape. Monet contributes the three
built-in subgraphs + the `RunState` contract; everything else is
LangGraph.

## Next steps

- [chat-extended](../chat-extended/) — capability-level extension
  (`@agent` + slash commands).
- [chat-default](../chat-default/) — stock chat REPL.
- [quickstart](../quickstart/) — baseline default pipeline.
