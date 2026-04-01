# Tech Spikes

Identified from red team analysis of the system architecture. Each spike is a concrete question that must be answered with working code before the corresponding component is built. Spikes are not features — they produce a decision and a reference implementation, not production code.

---

## Spike 1 — Node Wrapper Transport Switch

**Question**: How does `invoke_agent()` call an `@agent` decorated Python function directly in the co-located deployment and switch to an HTTP call in the distributed deployment, without the calling code changing?

**Why it matters**: The architecture states that agents switch from direct function call to HTTP via a config flag and nothing else changes. This seam has never been built or tested. If the branching requires different call signatures, different error handling, or different result shapes in each path, the abstraction is broken and must be redesigned before anything is built on top of it.

**Concrete deliverable**: A minimal working implementation of `invoke_agent()` that:
- Accepts `agent_id`, `command`, and an input envelope
- Calls a decorated Python function directly when the agent descriptor carries a callable reference
- Makes an HTTP POST to the agent's URL when the descriptor carries an endpoint
- Returns an identical `AgentResult` shape in both cases
- Is switched entirely by which descriptor type is loaded at startup

**Success criteria**: The same test that calls `invoke_agent()` passes unchanged against both a local `@agent` function and a mock HTTP server serving the same agent. No conditional branching in the test. No different assertions for each path.

**Risks to investigate**:
- Does `AgentRunContext` ContextVar propagate correctly when calling from a LangGraph node into a decorated function? Or does the node execution context interfere?
- For HTTP agents, does the `traceparent` header injection need to happen inside `invoke_agent()` or outside it?
- What is the error shape when the HTTP call fails vs when the function raises? Does `AgentResult` represent both identically?

---

## Spike 2 — Mock Agent Pattern

**Question**: What does a minimal Python agent look like that exercises the full call pattern — receives an input envelope, calls a real or stubbed LLM, processes a response, and returns an `AgentResult`?

**Why it matters**: Every test in the system will need to invoke agents. Without a shared, well-designed mock agent pattern, each test file will invent its own, producing inconsistent fixtures, duplicated setup, and brittle tests. The mock agent pattern must be defined once and reused everywhere.

**Concrete deliverable**: A pytest fixture module containing:
- A `minimal_agent` fixture — a decorated function that accepts a task, returns a fixed string, and exercises the full decorator path including `AgentRunContext` injection and `AgentResult` assembly
- An `artifact_agent` fixture — a decorated function that calls `write_artifact()` and returns a pointer, exercising the catalogue write path
- A `failing_agent` fixture — a decorated function that raises each typed exception in turn, exercising the signal population path
- A `slow_agent` fixture — a decorated function that emits progress events and sleeps, exercising the `emit_progress()` path

All fixtures should be usable with `pytest-asyncio`. The module should be importable from any test file with a single import.

**Success criteria**: Each fixture exercises its path in isolation. A test using `minimal_agent` does not need to know anything about the catalogue. A test using `artifact_agent` does not need to configure OTel. Fixtures compose cleanly — a test can use `artifact_agent` inside a LangGraph node without re-implementing the node wrapper.

**Risks to investigate**:
- Does `pytest-asyncio` require `asyncio_mode = "auto"` in `pyproject.toml` or per-test marking? Establish the convention once.
- Do ContextVar values set inside a pytest fixture leak between tests? The `clear_registry()` context manager is needed from day one.
- What is the right scope for the Langfuse/OTel test double — module scope, session scope, or per-test?

---

## Spike 3 — Catalogue Interface and Stub

**Question**: What is the abstract interface for the catalogue client that `write_artifact()` calls, such that tests can swap in a stub without the agent code changing between phases?

**Why it matters**: `write_artifact()` changes behaviour across deployment phases — in-memory stub during early testing, direct filesystem call during local development, HTTP call against the catalogue service in integration tests and production. If each transition requires changing the agent code, tests will not validate the production path. The interface must be stable and the implementation must be swappable by configuration, not by code changes.

**Concrete deliverable**: A working `CatalogueClient` abstract interface with three implementations:
- `InMemoryCatalogueClient` — stores artifacts in a dict, returns deterministic URLs, no I/O
- `FilesystemCatalogueClient` — writes to a temp directory, validates metadata schema, returns file:// URLs
- `HttpCatalogueClient` — calls the catalogue FastAPI service, handles 4xx/5xx, validates response

`write_artifact()` in the SDK accepts a `CatalogueClient` instance injected via the `AgentRunContext`, defaulting to the HTTP client configured from environment variables. Tests inject the in-memory client.

**Success criteria**: An agent test that uses `InMemoryCatalogueClient` produces the same `AgentResult` shape as an integration test using `HttpCatalogueClient`. The agent function does not import or reference any specific client implementation. Switching implementations requires only changing the fixture, not the agent.

**Risks to investigate**:
- Should the client be injected via `AgentRunContext` or via a module-level singleton? The ContextVar approach is cleaner for test isolation. The singleton is simpler but collides on concurrent tests.
- What is the minimal metadata the stub must validate? The write-time invariants must be enforced even in the stub or tests will pass against a looser schema than production enforces.
- How does the `ArtifactPointer` URL differ between implementations? Tests that assert on URL format will break when switching. Assert on the pointer ID, not the URL.

---

## Spike 4 — Confidence Calibration Strategy

**Question**: Given that agent-declared confidence scores are self-reported floats with no calibration mechanism, what is the minimum viable approach to detecting systematic miscalibration before it causes routing errors in production?

**Why it matters**: The architecture acknowledges confidence calibration as the weakest jidoka point. An agent that consistently declares 0.85 confidence on outputs that QA rejects 60% of the time is producing defective signals that the orchestrator routes on. Without a detection mechanism, this failure mode is invisible until QA pass rates collapse.

**Concrete deliverable**: A Langfuse query pattern that:
- Joins agent-declared confidence scores (from span attributes) against QA outcomes (from QA agent output envelopes) by `run_id`
- Computes a calibration error metric per agent per command over a rolling 30-day window
- Flags agents where declared confidence systematically deviates from actual QA pass rate by more than a threshold

This does not need to be automated initially. A runnable query and a documented review cadence is sufficient for the first iteration.

**Success criteria**: Given a synthetic dataset of 50 runs with known declared confidence and known QA outcomes, the query correctly identifies a deliberately miscalibrated agent and correctly clears a well-calibrated one.

**Risks to investigate**:
- QA outcomes are produced by the QA agent and live in its output envelope. How do they get linked to the producing agent's run? The `run_id` is the join key but the QA agent is a separate invocation. Verify this join is possible from Langfuse traces.
- Is 30 days enough data for statistical significance at typical run volumes? Define the minimum sample size before the metric is meaningful.
- What is the threshold for flagging miscalibration? This is a policy decision, not a technical one, but the spike should recommend a starting value.

---

## Spike 5 — Integration Test Strategy

**Question**: When does end-to-end testing start, and what does the minimal integration test look like that exercises the full path from LangGraph graph invocation through agent call through catalogue write through Langfuse trace?

**Why it matters**: Unit tests per component are necessary but insufficient. The integration points — node wrapper calling `invoke_agent()`, decorator writing to the catalogue, OTel spans appearing in Langfuse — will fail in ways that unit tests cannot detect. Without an integration test strategy defined early, these failures are discovered late.

**Concrete deliverable**: A single integration test that:
- Starts the FastAPI server with `InMemoryCatalogueClient` and a real OTel collector pointed at a local Langfuse instance (or a mock collector)
- Invokes a minimal LangGraph graph with one `@agent` decorated node
- Asserts that the graph state after completion contains a valid `AgentResult`
- Asserts that the catalogue received a write with valid metadata
- Asserts that an OTel span was emitted with the expected agent ID and command attributes

This test should run in CI on every pull request. It is the definition of "the integration seam works."

**Success criteria**: The test passes against SQLite + filesystem catalogue in CI. It is parameterised to also run against Postgres + S3 catalogue on merge to main. Switching the backing store requires only environment variable changes, not test code changes.

**Risks to investigate**:
- CI runs against SQLite but production runs Postgres. Define the Postgres CI job and ensure the catalogue schema migration runs as part of test setup.
- Local Langfuse requires Docker Compose. Define the `docker-compose.dev.yml` that developers run to get a full local stack before this spike starts.
- The OTel collector in tests should be a real collector pointed at a local Langfuse, not a mock. Mocking the collector validates nothing about the Langfuse integration.

---

## Cross-Cutting: Development Environment

Before any spike can proceed, the development environment must be defined. This is not a spike — it is a prerequisite.

**Required**: A `docker-compose.dev.yml` that runs:
- Postgres (for LangGraph checkpointer and catalogue index)
- Langfuse (for OTel traces)
- The FastAPI server (for agent endpoints and catalogue)

**Required**: A `pyproject.toml` with:
- `asyncio_mode = "auto"` for pytest-asyncio
- `hypothesis` for property-based testing of content limit edge cases
- `pytest-asyncio` configured from day one, not retrofitted later
- OTel as a hard dependency, not an optional extra

**Required**: Pre-commit hooks that run:
- `ruff` for linting (fast, replaces flake8 + isort + pyupgrade)
- `mypy` in daemon mode (avoids per-commit startup cost)
- `pytest -x --fast` (unit tests only, not integration tests)

Integration tests run in CI, not in pre-commit.