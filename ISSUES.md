# Known Issues

Bugs and design gaps not yet addressed. Roadmap features live in `CLAUDE.md` under `## Roadmap`; this file is for things that are broken, deprecated, or violating stated standards. Pick items from here when doing maintenance passes.

Each entry: **symptom**, **location**, **why it matters**, **fix sketch** where obvious.

---

## I8 — InterruptScreen: Tab/click do nothing, can't submit approve/reject

**Symptom.** When the chat HITL approval screen pops after `/plan`, pressing Tab, arrow keys, or clicking the Approve/Reject/Revise radio or Submit button produces no reaction. User is stuck with no way to progress the plan.

**Location.** `src/monet/cli/_chat_app.py` — `InterruptScreen` converted from `ModalScreen` to `Screen` to de-cramp the layout. Either the focus chain isn't picking up the new screen's widgets, or a parent-level binding (likely the `tab` binding on `ChatApp` for slash suggestions + `escape` for hide_suggest) is swallowing the events before the screen gets them.

**Why it matters.** The whole HITL approve/revise/reject loop is unreachable from chat — defeating the purpose of the interrupt refactor.

**Fix sketch.** Either (a) unbind `tab`/`escape` at the App level and move them onto the ChatApp's main Screen so sub-screens are unaffected, or (b) add an explicit focus on the first field in `InterruptScreen.on_mount`. Verify with a Textual `Pilot` test that drives a full approve path from the screen.

---

## I9 — Chat triage picks `planner/fast` when `researcher/deep` fits

**Symptom.** Free-form user turns like "give me a detailed report on AI trends in healthcare" get triaged to `planner` with `command="fast"` (the quick classifier), rather than dispatching directly to `researcher/deep` or routing to the full planning pipeline.

**Location.** `src/monet/orchestration/chat_graph.py` — `triage_node` builds a system prompt listing registered agent ids but does not list each agent's *commands* nor the per-command descriptions. The triage classifier can therefore only return `specialist=<agent_id>`, and the downstream `specialist_node` hardcodes `mode = command_meta.get("mode") or "fast"`. Deep-research tasks end up on the shallow command.

**Why it matters.** Research-grade chat requests silently downgrade to the fast agent path, producing lower-quality output without any indication to the user.

**Fix sketch.** Extend `ChatTriageResult` with a `command` field, include each `{agent_id, command, description}` in the grounding prompt, and have `specialist_node` use `command_meta["mode"]` without a `"fast"` fallback when triage names one. Preserve the fallback for slash-invoked specialists (`/researcher:deep` already sets `mode=deep` explicitly).

---

The prior I1–I7 slate plus I1 (chat graph phantom `planner/chat`) are resolved. New findings land here.

---

## Out of scope for this file

- **Roadmap features** (SaaS enabling primitives, push pool dispatch, pluggable pipeline adapters, in-process driver reintroduction, graph↔client wire-contract test, summarizer agent) live in `CLAUDE.md ## Roadmap` and `docs/architecture/roadmap.md`. Those are forward-looking commitments, not present-tense defects.
- **Resolved items** from prior sessions (catalogue sync-in-async, artifact double-write, server-process agent wiring, Langfuse OTLP setup, Windows CLI encoding, triage nondeterminism, resume/stream race, alembic pre-existing-DB crash, SignalType msgpack allowlist, triage suggested_agents validation, langchain-tavily migration, redis_streams mypy drift, E2E coverage gap, triage classification bias) were verified fixed in the current code and removed from this list.
