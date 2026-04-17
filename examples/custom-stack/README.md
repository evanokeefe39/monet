# custom-stack

Fully user-owned stack on top of monet core: bespoke agents, bespoke
chat graph, bespoke pipeline. Zero reuse of the monet reference
agents or the built-in `orchestration.chat` / `planning_graph` /
`execution_graph` subgraphs.

The example exists to pin the coupling surface between the monet CLI /
client / server and user code. Anything that still works when this
example replaces the defaults is, by construction, not coupled.

## Layout

```
examples/custom-stack/
├── pyproject.toml
├── monet.toml                # [chat].graph → myco.graphs.chat:build_chat_graph
├── aegra.json                # registers chat + custom_pipeline
├── server_graphs.py          # 0-arg factories Aegra calls at boot
├── .env.example
└── myco/
    ├── agents/               # bespoke agents — side-effect registered
    │   ├── _stub_llm.py      # deterministic canned responses (hermetic)
    │   ├── planner.py
    │   ├── researcher.py
    │   ├── writer.py
    │   └── conversationalist.py
    └── graphs/
        ├── chat.py           # bespoke chat graph (2 interrupt envelopes)
        └── pipeline.py       # bespoke pipeline
```

## Run

```bash
cp .env.example .env
uv sync
monet dev                     # provisions Postgres + starts Aegra
monet chat                    # opens the custom chat graph
monet run --graph custom_pipeline "any topic"
```

## Wire-contract surface a custom stack must preserve

These are the load-bearing conventions the monet CLI / client / server
depend on. The rest — node names, state keys, routing, agent count,
prompt shape, LLM provider — is entirely up to the user.

### 1. Chat message shape

The chat TUI reads `state["messages"]` and renders every entry as
`{role: "user"|"assistant"|"system", content: str}` dicts. Both the
user's graph and the server must write this shape when populating chat
state.

### 2. Interrupt form-schema envelope

Interrupts must carry a `{prompt, fields, context?}` envelope. `fields`
is a list of `{name, type, label, options?, default?, required?}`.
Closed field vocab: `text | textarea | int | bool | radio | select |
checkbox | hidden`. The TUI renders any envelope in this shape; it
does not care about the semantics.

### 3. Resume payload shape

On resume, the TUI posts back a dict keyed by `field.name`. The user's
graph is responsible for interpreting it. There is no special casing
for "approval" — it's just a form with a radio field.

### 4. Entrypoint declaration

Invocable graphs must be declared in `monet.toml [entrypoints.<name>]`
with `graph = "<id>"`. `monet run --graph <name>` and
`MonetClient.run(<name>, ...)` read this.

### 5. Chat graph must exist

`monet chat` fails fast at startup if the chat graph is not registered
on the server. Either configure `[chat] graph = "module:factory"` or
ensure the default is reachable. The check surfaces an actionable error
before the TUI opens.

### 6. Agent registration

Agents register via `@agent(...)` decorators. The server must import
the module containing the decorators before serving requests — done
here in `server_graphs.py` via `import myco.agents`.

### 7. Artifact API

Agents writing artifacts must use `ctx.write_artifact(key, bytes,
media_type)`. Downstream consumers locate artifacts by key via
`find_artifact(result.artifacts, key)`. No position-based access.

## What is NOT on the wire-contract list

- Node names. The user's graph can call its nodes anything.
- State keys beyond `messages`. The user's graph defines its own state.
- Signal vocabulary. The user's agents emit any signals they like.
- Agent IDs. No reference agent IDs are reserved.
- Routing / phases / waves / HITL policy. All user code.
- Specific interrupt kinds. Users ship as many distinct envelopes as
  they want; the TUI renders them identically.

## Testing

End-to-end coverage lives at `tests/e2e/test_e2e_custom_stack.py` and
runs under `MONET_E2E=1`. It drives the custom graph via `MonetClient`
with no TUI so the test is fully scripted.
