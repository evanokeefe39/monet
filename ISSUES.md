# Known Issues

Bugs and design gaps not yet addressed. Roadmap features live in `CLAUDE.md` under `## Roadmap`; this file is for things that are broken, deprecated, or violating stated standards. Pick items from here when doing maintenance passes.

Each entry: **symptom**, **location**, **why it matters**, **fix sketch** where obvious.

---

_No open issues._

The prior I1–I7 slate (SignalType msgpack allowlist, triage suggested_agents validation, langchain-tavily migration, redis_streams mypy drift, E2E coverage gap, triage classification bias) is resolved. New findings land here.

---

## Out of scope for this file

- **Roadmap features** (SaaS enabling primitives, push pool dispatch, pluggable pipeline adapters, in-process driver reintroduction, graph↔client wire-contract test, summarizer agent) live in `CLAUDE.md ## Roadmap` and `docs/architecture/roadmap.md`. Those are forward-looking commitments, not present-tense defects.
- **Resolved items** from prior sessions (catalogue sync-in-async, artifact double-write, server-process agent wiring, Langfuse OTLP setup, Windows CLI encoding, triage nondeterminism, resume/stream race, alembic pre-existing-DB crash, SignalType msgpack allowlist, triage suggested_agents validation, langchain-tavily migration, redis_streams mypy drift, E2E coverage gap, triage classification bias) were verified fixed in the current code and removed from this list.
