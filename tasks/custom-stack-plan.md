# custom-stack example + chat fail-fast

Goal: prove CLI / MonetClient / server are not coupled to a specific
agent or graph implementation. The only invariant is that a chat graph
must be registered if `monet chat` is invoked. Fail fast when it is not.

## Phase 1 — scaffold `examples/custom-stack/`

Fully custom stack. Zero reuse of `monet.orchestration.chat`,
`monet.orchestration.planning_graph`, `monet.orchestration.execution_graph`,
or any `monet.agents.*`.

- [ ] `pyproject.toml` — local editable monet dep, no `[examples]`
      extras needed since agents are stubbed
- [ ] `monet.toml` — `[chat] graph = "myco.graphs.chat:build_chat_graph"`
      + `[entrypoints.custom_pipeline] graph = "custom_pipeline"`
- [ ] `aegra.json` — register `chat` + `custom_pipeline`
- [ ] `server_graphs.py` — 0-arg wrappers (Aegra quirk), import agents
      for side-effect registration
- [ ] `.env.example` — placeholders only; stub LLM needs no keys
- [ ] `src/myco/agents/_stub_llm.py` — canned responses; hermetic
- [ ] `src/myco/agents/planner.py` — writes `work_brief` artifact +
      emits `NEEDS_CLARIFICATION` (only if reusing planning subgraph —
      but we're not, so this is pure custom shape)
- [ ] `src/myco/agents/researcher.py`
- [ ] `src/myco/agents/writer.py`
- [ ] `src/myco/agents/conversationalist.py` — drives chat respond
- [ ] `src/myco/graphs/chat.py` — bespoke chat graph; emits 2 distinct
      interrupt envelopes (`plan_approval` + `risk_review`) to prove
      TUI renders any form-schema
- [ ] `src/myco/graphs/pipeline.py` — bespoke pipeline
- [ ] `README.md` — enumerates the wire-contract surface a custom stack
      must preserve

## Phase 2 — fail-fast on `monet chat`

Today `monet chat` only catches `ConnectError` on the first request.
Move both checks upfront so operators see actionable errors before
the TUI swallows the terminal.

- [ ] `src/monet/cli/_chat.py`: before `ChatApp.run_async`, probe
      `{url}/health` and fail with the existing "cannot reach" message
      if no 200
- [ ] Probe chat graph is registered via `client.list_graphs()`; fail
      with an actionable message if the resolved chat graph id is
      absent
- [ ] Messages point operators at `monet dev`, `monet register`, or
      the `[chat] graph` config key as appropriate

## Phase 3 — tests

- [ ] `tests/test_chat_fail_fast.py` — two failure modes: server down
      + chat graph missing
- [ ] `tests/e2e/test_e2e_custom_stack.py` — `monet dev` in
      custom-stack/, plan_approval + risk_review round-trips, custom
      conversationalist reply, only custom agents in capabilities
- [ ] `uv run pytest` + `uv run ruff check .` + `uv run mypy src/`
      all green

## Phase 4 — review

- [ ] Review section updated here
- [ ] `tasks/follow-up-coupling-cleanup.md` still accurate for the
      separate next pass

## Review

Done in one pass.

- `examples/custom-stack/` scaffolded with bespoke agents
  (`myco_planner`, `myco_researcher`, `myco_writer`,
  `myco_conversationalist`), bespoke chat graph with two distinct
  interrupt envelopes (`plan_approval`, `risk_review`), bespoke
  two-step pipeline, hermetic stub LLM, aegra.json + monet.toml
  wiring, README enumerating the 7-item wire-contract surface. Zero
  reuse of `monet.orchestration.chat*` / `planning_graph` /
  `execution_graph` / `monet.agents.*`.
- `src/monet/cli/_chat.py` now fails fast with actionable errors
  before the TUI opens when (a) the server is unreachable, (b) the
  server refuses graph enumeration, or (c) the resolved chat graph id
  is not registered. Health probe is shared with `monet run` via
  `_preflight_server`.
- `tests/test_chat_fail_fast.py` covers all three failure modes (3/3
  passing).
- `tests/e2e/test_e2e_custom_stack.py` covers capabilities surface,
  conversational path, two-envelope HITL round-trip, plan rejection
  short-circuit, pipeline entrypoint — all under `MONET_E2E=1`.
- `uv run pytest` 624 pass / 18 skipped; `ruff check` clean; `mypy
  src/` clean.
- Follow-on work (coupling cleanup) captured standalone in
  `tasks/follow-up-coupling-cleanup.md` — 10 ranked items.
