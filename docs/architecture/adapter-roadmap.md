# Adapter & Integration Roadmap

Ordered by effort and dependency. Each phase builds on the previous. Easy wins first.

---

## Phase 1: Prove What We Have (1-2 days)

Everything here uses existing code. No new features. Just validation.

### 1.1 Browser-Use as native Python agent
**Effort:** Trivial — Browser-Use is a Python package, no transport needed.
**What to do:**
- `uv add browser-use` in a new example
- Write a thin `@agent` wrapper that takes a task string, calls `browser_use.Agent(task=task).run()`, returns the result
- Test: "extract the top 5 headlines from news.ycombinator.com"
- Proves: native Python agents from the ecosystem work with zero adapter friction

**What we learn:** Whether our agent contract (task string in, result out) maps cleanly to real agents or if there are impedance mismatches (async context, browser lifecycle, timeouts).

### 1.2 Hermes via existing sse_post transport
**Effort:** Low — Hermes speaks OpenAI format, we have SSE POST.
**What to do:**
- Run Hermes locally via Docker (`docker run nousresearch/hermes-agent`)
- Write an `agents.toml` entry pointing at `http://localhost:8642/v1/chat/completions`
- Write a minimal transform function that maps OpenAI `chat.completion.chunk` SSE events to monet event protocol
- Test: send a research task, collect streamed response

**What we learn:** Whether `sse_post` handles OpenAI SSE framing (it probably doesn't — OpenAI uses `data: [DONE]` sentinel and `choices[0].delta.content` nesting). This will reveal the exact transform logic needed.

### 1.3 Long-running task smoke test
**Effort:** Low — just configuration and observation.
**What to do:**
- Write a dummy agent that sleeps for 10 minutes, emitting progress every 30 seconds
- Run it with default config → document what breaks (timeout, lease expiry, progress drops)
- Run it with correct config (`--task-timeout 900`, lease TTL 900) → document what works
- Record the exact flags and config needed

**What we learn:** The real operator experience for long tasks. What's obvious, what's surprising, what needs better defaults or validation.

### 1.4 AgentStream subprocess E2E test
**Effort:** Low — write a real test.
**What to do:**
- Create a tiny Python script that reads stdin JSON, emits progress/artifact/result events to stdout
- Write an integration test that invokes it via `AgentStream.cli()` inside a real `@agent` handler
- Verify: progress reaches `emit_progress`, artifacts land in store, result comes back

**What we learn:** Whether the full stack (AgentStream → default handlers → ContextVar ambient functions → AgentResult) works end-to-end, not just in unit test isolation.

---

## Phase 2: Fix the Gaps the Tests Reveal (3-5 days)

Based on what Phase 1 exposes. Predicted issues:

### 2.1 OpenAI SSE format transform
**Effort:** Medium — well-defined scope.
**What to do:**
- Implement a reusable transform function: `openai_chat_to_monet(event: dict) -> dict | None`
- Handle: `chat.completion.chunk` → accumulate deltas → emit monet `progress` events
- Handle: `[DONE]` sentinel → emit monet `result` with assembled content
- Handle: tool_calls in delta → emit monet `artifact` events
- Ship as `monet.transforms.openai` so any OpenAI-compatible agent (Hermes, vLLM, Ollama, LiteLLM) gets it for free

**Files:** New `src/monet/transforms/__init__.py` and `src/monet/transforms/openai.py`

### 2.2 Transform hook in agents.toml
**Effort:** Low — ~30 lines in agent_loader.py.
**What to do:**
- Add `transport.transform = "module:func"` field
- In `_make_handler()`, wrap stream iteration to pipe events through transform
- Reuse `_import_python_handler` resolution logic
- Test with the OpenAI transform from 2.1

**Files:** `src/monet/core/agent_loader.py`

### 2.3 Auth headers in agents.toml
**Effort:** Low — ~20 lines.
**What to do:**
- Add `[agent.transport.headers]` table support
- Implement `${ENV_VAR}` interpolation via `os.environ`
- Pass resolved headers to AgentStream constructors via `**kwargs`
- Test with a mock authenticated endpoint

**Files:** `src/monet/core/agent_loader.py`

### 2.4 Long-running task documentation and defaults
**Effort:** Low — docs and maybe one validation guard.
**What to do:**
- Document the configuration matrix: task_timeout × lease_ttl × heartbeat_interval
- Add a startup warning if task_timeout > lease_ttl (silent failure today)
- Add a "long-running tasks" section to the orchestration guide
- Consider raising default task_timeout to something more realistic (600s? configurable per agent?)

**Files:** `src/monet/worker/_loop.py`, `docs/guides/orchestration.md`

---

## Phase 3: First Real Demo (3-5 days)

### 3.1 Browser-Use research pipeline demo
**Effort:** Medium — composition of proven pieces.
**What to do:**
- Pipeline: browser_extract agent → qa_validator agent → summarizer agent
- browser_extract: wraps Browser-Use, extracts structured data from a URL
- qa_validator: checks extraction against task requirements, emits LOW_CONFIDENCE signal if mismatch
- summarizer: condenses validated extraction into a report
- HITL gate between qa_validator and summarizer when confidence is low
- Write `agents.toml` for browser_extract, native Python for qa/summarizer
- Include `railway.toml` and `docker-compose.yml`
- README with one-command local run and Railway deploy button

**Directory:** `examples/browser-research/`

**Why this demo:** Shows real value — Browser-Use can't do QA on its own output. monet adds the quality layer. Visual, tangible, deployable.

### 3.2 Hermes research with approval gates demo
**Effort:** Medium — depends on 2.1 (OpenAI transform).
**What to do:**
- Pipeline: hermes_researcher agent → fact_checker agent → report_writer agent
- hermes_researcher: calls Hermes via OpenAI-compatible API, long-running (5-10 min)
- fact_checker: validates claims against sources
- HITL gate before any action on findings
- Demonstrate the long-running task config from 2.4
- Include `railway.toml`, `docker-compose.yml` with Hermes container

**Directory:** `examples/hermes-research/`

**Why this demo:** Shows monet orchestrating the second most popular agent framework. Hermes users see immediate value: keep Hermes's research capability, add structural review.

---

## Phase 4: Transport Additions (5-7 days)

### 4.1 WebSocket transport
**Effort:** Medium.
**What to do:**
- Add `AgentStream.websocket(url, payload)` constructor
- Implement `_iter_websocket()` using `websockets` library (lazy import)
- Add `"ws"` to `_VALID_TRANSPORT_TYPES` in agent_loader.py
- Add branch in `_build_stream()`
- Unit test with a mock WebSocket server

**Files:** `src/monet/streams.py`, `src/monet/core/agent_loader.py`

**Why now:** Prerequisite for OpenClaw adapter.

### 4.2 MCP transport
**Effort:** High — MCP has handshake, JSON-RPC framing, tool discovery.
**What to do:**
- Add `AgentStream.mcp(cmd)` for stdio MCP servers
- Add `AgentStream.mcp_sse(url)` for SSE MCP servers
- Handle initialize handshake automatically
- Map monet task → `tools/call` (tool name from config)
- Map MCP content arrays → monet artifact events
- Map MCP notifications/progress → monet progress events
- Add `"mcp"` and `"mcp_sse"` to valid transport types
- Optional dependency: `mcp` Python package

**Files:** `src/monet/streams.py` (or new `src/monet/_mcp_transport.py`), `src/monet/core/agent_loader.py`

### 4.3 SSE reconnection support
**Effort:** Medium.
**What to do:**
- Add `Last-Event-ID` tracking to `_iter_sse()` and `_iter_sse_post()`
- On connection drop, reconnect with `Last-Event-ID` header
- Server-side: verify event ID emission in progress stream endpoints
- Configurable max retries and backoff

**Files:** `src/monet/streams.py`

---

## Phase 5: Advanced Demos (5-7 days)

### 5.1 OpenClaw sandboxed task runner
**Effort:** High — depends on 4.1 (WebSocket transport).
**What to do:**
- OpenClaw gateway adapter module: handles challenge-response auth, RPC framing, scope negotiation
- Worker pool with restricted Docker mount (read-only fs, one writable output dir)
- Demo: OpenClaw handles a file processing task, monet contains the blast radius
- Show what happens when OpenClaw attempts to exceed scope (signal fires, HITL gate)

**Directory:** `examples/openclaw-sandboxed/`

### 5.2 MCP agent marketplace demo
**Effort:** Medium — depends on 4.2 (MCP transport).
**What to do:**
- Wrap 3 different MCP servers as monet agents via agents.toml
- Show dynamic composition: planner routes to whichever MCP agent has the right tool
- Goose as one of the MCP agents

**Directory:** `examples/mcp-agents/`

### 5.3 Agent shootout / scorecard demo
**Effort:** Medium — new feature + demo.
**What to do:**
- Minimal scorecard: track success rate, duration, signal frequency per agent/command
- Store in SQLite (same pattern as progress store)
- Same task dispatched to two agents, QA evaluates both, scorecard updated
- Dashboard page showing agent performance over time
- Proves the "performance manage your agents" story

**Directory:** `examples/agent-scorecard/`
**Files:** New `src/monet/scoring/` module

---

## Phase 6: Operational Maturity (ongoing)

### 6.1 Pool isolation documentation
- Be honest: pools are routing labels, not security boundaries
- Document the customer's responsibility: Docker isolation, network policies, IAM
- Provide Terraform/Helm templates for common isolation patterns
- Consider: should monet validate that a pool's worker has restricted capabilities? (future)

### 6.2 Split-plane deployment guide
- Step-by-step guide for deploying control plane (SaaS) + data plane (customer VPC)
- Terraform module for AWS (ECS control plane, ECS data plane, separate VPCs)
- Helm chart for Kubernetes
- E2E test that runs across the split

### 6.3 Agent supply chain tooling
- `monet agent add <name>` CLI command
- Agent health checks at registration time
- Known-issues registry (curated metadata about agent limitations)
- Version pinning in agents.toml

### 6.4 Protocol adapters (A2A, ACP)
- Monitor adoption of A2A and ACP
- If A2A reaches critical mass, add `AgentStream.a2a()` transport
- ACP support comes nearly free if MCP transport is done (same JSON-RPC/stdio framing)

---

## Tracking

| Phase | Item | Status | Blocking |
|-------|------|--------|----------|
| 1.1 | Browser-Use native wrapper | Not started | — |
| 1.2 | Hermes via sse_post | Not started | — |
| 1.3 | Long-running smoke test | Not started | — |
| 1.4 | AgentStream subprocess E2E | Not started | — |
| 2.1 | OpenAI SSE transform | Not started | 1.2 |
| 2.2 | Transform hook in agents.toml | Not started | 2.1 |
| 2.3 | Auth headers in agents.toml | Not started | — |
| 2.4 | Long-running task docs | Not started | 1.3 |
| 3.1 | Browser-Use pipeline demo | Not started | 1.1 |
| 3.2 | Hermes research demo | Not started | 2.1, 2.2 |
| 4.1 | WebSocket transport | Not started | — |
| 4.2 | MCP transport | Not started | — |
| 4.3 | SSE reconnection | Not started | — |
| 5.1 | OpenClaw sandboxed demo | Not started | 4.1 |
| 5.2 | MCP marketplace demo | Not started | 4.2 |
| 5.3 | Agent scorecard demo | Not started | — |
| 6.1 | Pool isolation docs | Not started | — |
| 6.2 | Split-plane guide | Not started | — |
| 6.3 | Agent supply chain CLI | Not started | — |
| 6.4 | A2A/ACP adapters | Not started | 4.2 |
