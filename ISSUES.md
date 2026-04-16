# Known Issues

Bugs and design gaps not yet addressed. Roadmap features live in `CLAUDE.md` under `## Roadmap`; this file is for things that are broken, deprecated, or violating stated standards. Pick items from here when doing maintenance passes.

Each entry: **symptom**, **location**, **why it matters**, **fix sketch** where obvious.

---

## Bugs / deprecation

### I1 — `SignalType` enum not registered with langgraph `allowed_msgpack_modules`

**Symptom:** every checkpoint resume emits a langgraph deprecation warning. A future langgraph release will hard-fail on unregistered enums during msgpack serialization.

**Location:** `src/monet/signals.py`, `src/monet/types.py` define `SignalType`. No registration call anywhere (grep `allowed_msgpack_modules` returns nothing in `src/`).

**Why it matters:** silent ticking time bomb on a dependency upgrade. State passes through msgpack on every HITL resume, so this affects every production run that uses interrupts.

**Fix sketch:** register the enum at server bootstrap (`src/monet/server/_bootstrap.py`) via langgraph's `allowed_msgpack_modules` API, or change the state shape to store signal types as plain strings. The former is less invasive.

### I2 — Triage `suggested_agents` not constrained to registered roster

**Symptom:** planner triage can return agent IDs that are not registered, causing routing skeletons to target nonexistent agents. Template asks the model nicely but does not enforce membership.

**Location:** `src/monet/agents/planner/templates/triage.j2:15` lists roster as plain text; `src/monet/orchestration/entry_graph.py:46` accepts parsed JSON without validation against `AgentManifest`.

**Why it matters:** a drifted `suggested_agents` list produces a `RoutingSkeleton` whose nodes fail at `invoke_agent` time with `CAPABILITY_UNAVAILABLE`, wasting a full execution wave before the error surfaces.

**Fix sketch:** switch `TriageResult.suggested_agents` to `list[Literal[*registered_ids]]` at pydantic-parse time, or validate membership in `entry_graph` after parse and reject/repair before handing off to planning.

### I3 — `langchain_community.tools.tavily_search.TavilySearchResults` deprecated

**Symptom:** researcher emits a langchain deprecation warning every run. Upstream migration target is the `langchain-tavily` package.

**Location:** `src/monet/agents/researcher/__init__.py:54` uses the deprecated import; comment at `:7` still references the old module.

**Why it matters:** will break on the next langchain-community release that removes the shim. Also makes CI logs noisy.

**Fix sketch:** add `langchain-tavily` to runtime dependencies (requires explicit approval per CLAUDE.md), swap the import, update the comment.

---

## Standards violations

### I4 — 18 mypy errors in `src/monet/queue/backends/redis.py`

**Symptom:** `uv run mypy src/` reports 18 errors, all in one file. All stem from redis-py's overloaded return types (`Awaitable[X] | X`) not matching the `await` call sites.

**Location:** `src/monet/queue/backends/redis.py` at lines 171, 203, 239, 257–258, 387, 405, 408, 455, 506 (and others).

**Why it matters:** CLAUDE.md "Code standards" mandates `mypy strict mode, zero errors required`. The pre-commit hook only type-checks staged files, so these drifted in while passing per-commit gates. Full-repo type-check is red.

**Fix sketch:** either add targeted `# type: ignore[misc]` with explanatory comments for the unavoidable overload ambiguities, or use `redis.asyncio`'s typed wrappers more carefully. Not a behavioral bug — the code works — but mypy is currently lying about the baseline.

---

## Design gaps

### I5 — No end-to-end integration test coverage across deployment topologies

**Symptom:** test suite is unit + component only. No test exercises the full dev → run path.

**Location:** `tests/` has no E2E suite. `CLAUDE.md ## Unimplemented` enumerates seven scenarios that need coverage.

**Why it matters:** recent refactors (catalogue → artifacts, client decoupling, pointer-only orchestration) changed cross-process wiring significantly. Regressions in those seams (e.g. quickstart-empty-plan) have only been caught by hand-testing. A missing E2E net means the next cross-cut refactor has the same exposure.

**Scenarios to cover** (from CLAUDE.md):
1. `monet dev` → `monet run` full default pipeline with HITL approve/revise/reject
2. `aegra serve` with external Postgres
3. Multiple concurrent `monet worker` instances claiming from the same server
4. `MONET_QUEUE_BACKEND=redis` and `sqlite` backends under load
5. Custom graph registration via `aegra.json` with non-monet graphs driven via `--graph`
6. Worker reconnection after server restart
7. `monet run --auto-approve` happy path end-to-end

**Fix sketch:** add `tests/e2e/` marked with a pytest marker that is skipped by default in CI unit runs. Use `testcontainers` for Postgres/Redis. Cover items 1 and 7 first — they exercise the most wiring per test.

### I7 — Triage classification bias: multi-step topics classified as "simple"

**Symptom:** the planner/fast triage prompt classifies explicitly multi-step requests as `complexity: "simple"` often enough to make HITL e2e tests nondeterministic. Observed on Gemini 2.5 Flash with topics like "Produce a comparative analysis of leading open-source LLM agent orchestration frameworks, including a strengths/weaknesses matrix and a recommendation for production use." — classified `simple` on ~1/3 of runs, which short-circuits the whole pipeline past planning before any interrupt can fire.

**Location:** `src/monet/agents/planner/templates/triage.j2` — prompt asks the model to pick from `simple | bounded | complex` with terse rules and no few-shot examples. `src/monet/orchestration/default_graph.py:_route_after_entry` short-circuits on `simple`, so any false-positive "simple" skips planning + execution.

**Why it matters:** the triage step is the gatekeeper for the whole pipeline. Incorrect classifications either cause premature END (observed) or wasted work (complex classification of trivial requests). This is an agent-quality issue — monet's philosophy says agent quality is the agent's responsibility — but the stock reference agents set the perceived baseline of the SDK.

**Fix sketch:** two complementary moves. (1) Add few-shot examples to the triage prompt biased toward the classifications we want for the examples/docs test-cases (a 3-sentence brief is `bounded`, a comparative analysis is `complex`). (2) Use `with_structured_output` so the model has to commit to a Literal value and cannot drift into freeform text that falls back to the parse-failure default. Both fit under the "reference agent quality pass" roadmap item.

---

## Out of scope for this file

- **Roadmap features** (SaaS enabling primitives, push pool dispatch, pluggable pipeline adapters, in-process driver reintroduction, graph↔client wire-contract test, summarizer agent) live in `CLAUDE.md ## Roadmap` and `docs/architecture/roadmap.md`. Those are forward-looking commitments, not present-tense defects.
- **Resolved items** from prior sessions (catalogue sync-in-async, artifact double-write, server-process agent wiring, Langfuse OTLP setup, Windows CLI encoding, triage nondeterminism, resume/stream race, alembic pre-existing-DB crash) were verified fixed in the current code and removed from this list.
