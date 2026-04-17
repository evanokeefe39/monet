# follow-up — chat/client coupling cleanup

Separate pass from the custom-stack scaffold. The scaffold proves
decoupling is *achievable today with discipline*. This pass reduces the
invariant surface the discipline must guard, so a user who brings their
own graph and agents cannot fail silently.

Source: architecture review 2026-04-17 (DR+ST+CQ findings on
`src/monet/cli/_chat*.py`, `src/monet/client/__init__.py`,
`src/monet/orchestration/chat/`, `_forms.py`, `_state.py`).

Ordered by leverage (biggest coupling reduction first). Each item is
independently shippable. Do not batch.

## 1. `InterruptEnvelope` as the published wire contract

Replace `Form`/`Field` TypedDict(total=False) in
`src/monet/client/_events.py` with a validated pydantic model published
in `monet.types`. Graph-side `_forms.py` builders return typed objects;
TUI consumes typed objects; server-side pydantic validator rejects
malformed envelopes at interrupt time so graphs cannot emit dicts that
render to garbage.

Field vocab is closed:
`text | textarea | int | float | bool | select | radio | checkbox | date |
artifact_ref | markdown | hidden`. Unknown type → fallback renderer
warns (forward-compatible).

Also: introduce `protocol_version: Literal[1]` on the envelope. Future
vocab additions bump the version.

## 2. Single-source `ApprovalAction`

`ApprovalAction = Literal["approve", "revise", "reject"]` lives in
`src/monet/orchestration/_forms.py`. Today `_chat_app._parse_approval_reply`
re-literals the same strings (`_chat_app.py:258-263`). Import from
`_forms`. Same for `_is_approval_form` (`_chat_app.py:211-223`): make it
call a classmethod on the envelope model rather than sniffing
`field["name"] == "action"`.

## 3. Typed `ChatMessage`

Single TypedDict in `monet.types`:
`ChatMessage = TypedDict({role: Literal["user","assistant","system"],
content: str})`. Consume at every crossing: `client/__init__.py:637-655,
698, 711`, `_chat.py:557-558`, `_chat_app.py:1021-1025,1115-1117`,
`chat/_parse.py`, `chat/_specialist.py`, `chat/_lc.py`, `chat/_format.py`,
`chat/_state.py:18`. Drop `dict[str, object]` annotations at every
consumer.

## 4. Fix `create_chat` metadata desync

`client/__init__.py:558,566,728` hardcode `MONET_GRAPH_KEY="chat"`
regardless of `self._chat_graph_id`. When a user configures
`[chat] graph = "myco.graphs.chat:build"` the thread metadata tag and
the stream target diverge. Pipe `self._chat_graph_id` into the metadata
write; filter `list_chats` + `_cmd_list_runs` by the same id.

## 5. Split `MonetClient` into `MonetClient` + `ChatClient`

`client/__init__.py` is 789 lines mixing three responsibilities: run
lifecycle (`run/resume/abort`), introspection (`list_runs`,
`list_capabilities`, `list_graphs`), chat CRUD. Extract the chat block
(`:554-735`) into `monet.client.chat.ChatClient` composed via
`MonetClient.chat: ChatClient`.

## 6. Split `ChatApp`

`cli/_chat_app.py` is 1192 lines mixing CSS, HITL parsing, transcript,
pulse animation, slash completion, picker screens. Extract:

- `_chat_view.py` — `RichLog` + role styling
- `_chat_pulse.py` — pulse animation controller
- `_chat_pickers.py` — `_PickerScreen` + list-style commands
- `_chat_slash.py` — `RegistrySuggester` + `SlashCommandProvider`
- `_chat_hitl.py` — form parsing + resume payload construction
- `_chat_app.py` — the `App` shell wiring them together

## 7. Narrow TUI `except Exception` handlers

Nine `contextlib.suppress(Exception)` sites + a dozen bare `except
Exception:` handlers silently swallow server errors. Per site: decide
"widget unmounted" (keep suppress) vs "network/state error" (log via
`_log.exception` so the chat log file captures it).

Specifically: `_cmd_list_runs`, `_cmd_list_threads`, `_cmd_list_agents`,
`_cmd_switch_thread`, `_cmd_new_thread`, `_load_thread_name`,
`get_chat_interrupt` fallback, `refresh_slash_commands`,
`MonetClient.list_chats` message-count loop.

## 8. Remove `_chat_app._parse_text_reply` multi-field branch

`_chat_app.py:294-301` handles a multi-field non-approval form shape
that no current graph emits. Once `InterruptEnvelope` lands (item 1),
delete the unreachable branch.

## 9. Resolve `chat_graph.py` shim

`src/monet/orchestration/chat_graph.py` is a 24-line compat shim
re-exporting from `chat/`. `ChatConfig._DEFAULT_CHAT_GRAPH` points at the
shim, not the real module. Either delete the shim and update the default,
or keep the shim and document why. Not both.

## 10. Extend `monet.types` public exports

Ship `ChatMessage`, `InterruptEnvelope`, `Field`, `FieldOption`,
`ApprovalAction` (already declared) as re-exports from `monet.types`.
Document the stability contract in `docs/api/core.md`.

## 11. Lazy reference-agent import in `default_graphs.py`

`src/monet/server/default_graphs.py:21` does `import monet.agents`
at module level. This fires as soon as any process imports the
module — including `_resolve_graph_paths` during `monet dev`
startup. Result: a user's server that ships only custom agents
still registers the reference agents (planner, researcher, writer,
qa, publisher). `list_capabilities` returns both sets, slash
suggestions pollute, and introspection tooling cannot distinguish
"user brought these" from "monet shipped these".

Fix: move `import monet.agents` into the 0-arg `build_default_graph`
factory so it only runs when the default pipeline is actually
compiled. Users whose aegra.json doesn't mount the default pipeline
get a clean registry. Surfaced during the custom-stack e2e test —
see `tests/e2e/test_e2e_custom_stack.py::test_custom_agents_registered_alongside_defaults`
which must stay softened until this ships.

## Verification

After each item lands:

1. `uv run pytest` green
2. `uv run ruff check .` + `uv run mypy src/` green
3. The custom-stack example still works end-to-end
   (`tests/e2e/test_e2e_custom_stack.py`)
4. The `docs/architecture/` coupling note is updated with the reduced
   contract surface

Success metric: `examples/custom-stack/README.md`'s "wire-contract
surface" list shrinks by at least 5 items.
