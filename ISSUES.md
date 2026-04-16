# Known Issues

Bugs and design gaps not yet addressed. Roadmap features live in `CLAUDE.md` under `## Roadmap`; this file is for things that are broken, deprecated, or violating stated standards. Pick items from here when doing maintenance passes.

Each entry: **symptom**, **location**, **why it matters**, **fix sketch** where obvious.

---

## I10 — No progress updates visible in chat transcript

**Symptom.** Agents emit `emit_progress({...})` during execution (e.g. researcher emits `"researching (fast)"`, `"writing artifact"`). These never appear in the `monet chat` transcript. User sees `[info] thinking…` then the final `[assistant] …` reply with no intermediate feedback. Long-running turns (deep research, multi-step plans) look frozen.

**Location.** `src/monet/cli/_chat_app.py` — `_drain_stream` iterates only over `send_message`'s yielded strings (assistant message content). `monet.client._stream_chat_with_input` reads LangGraph `updates` mode and yields assistant messages from `messages` patches, ignoring `AgentProgress` events entirely. `monet.client` also supports a `custom` stream mode via `stream_run` in `_wire.py` that carries progress payloads — not wired into the chat path.

**Why it matters.** Users staring at a spinner for 30 s assume it's broken. Also masks useful real-time signal (which agent is working, which phase). Undermines the "transparent orchestration" claim.

**Fix sketch.** Two options:
- (a) Extend `_stream_chat_with_input` to also surface progress chunks — subscribe to `stream_run`'s `custom` mode and yield `AgentProgress` events as a typed value the chat app can tag differently (`[progress] researching…`). Requires a multi-type async iterator (e.g. `Union[str, AgentProgress]`) or a sidecar generator.
- (b) Add a new `MonetClient.chat_progress_stream(thread_id)` generator that polls `/tasks/{task_id}/progress` or subscribes to Redis Pub/Sub, runs concurrently in the TUI (background task) and writes `[progress] …` lines into the transcript.

Lean (a) — reuses the existing stream; no new transport. Tag progress lines distinctly in `_TAG_STYLES` (e.g. `[progress]` in muted yellow) so they visually separate from assistant output.

---

The prior I1–I9 slate is resolved. New findings land here.

---

## Out of scope for this file

- **Roadmap features** (SaaS enabling primitives, push pool dispatch, pluggable pipeline adapters, in-process driver reintroduction, graph↔client wire-contract test, summarizer agent) live in `CLAUDE.md ## Roadmap` and `docs/architecture/roadmap.md`. Those are forward-looking commitments, not present-tense defects.
- **Resolved items** from prior sessions (catalogue sync-in-async, artifact double-write, server-process agent wiring, Langfuse OTLP setup, Windows CLI encoding, triage nondeterminism, resume/stream race, alembic pre-existing-DB crash, SignalType msgpack allowlist, triage suggested_agents validation, langchain-tavily migration, redis_streams mypy drift, E2E coverage gap, triage classification bias) were verified fixed in the current code and removed from this list.
