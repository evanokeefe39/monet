# Multi-Agent System Architecture

---

## Design Principles

**From Mario Zechner (pi-agent)**
- Build only what you need — every feature has a carrying cost in context, complexity, and maintenance
- Full observability is non-negotiable — you must see exactly what goes into the model's context, what came out, and what tools were called
- Minimal stable interfaces — small, explicitly declared contracts that do not change under you
- Context engineering is the real work — exactly controlling what the model sees yields better outputs than prompt gymnastics
- Composability through layering, not inheritance — distinct layers that do not know about each other's internals
- Sessions are first-class serialisable artifacts — everything is inspectable, replayable, and post-processable

**From Toyota (14 principles — those applicable to system design)**
- Principle 1: Long-term philosophy over short-term gain — restraint in what is built now, design seams for what will be needed later
- Principle 2: Continuous flow — work passes between agents as artifacts, not batched. Problems surface immediately rather than accumulating
- Principle 3: Pull not push — agents are invoked on demand when the graph needs output. No pre-emptive execution
- Principle 5: Jidoka — quality gates built into the system, not delegated to self-report. QA is structural, HITL policy is orchestrator-owned
- Principle 6: Standardisation as the foundation for composability — uniform agent interface, stable artifact schema, consistent output envelope
- Principle 7: Visual control, nothing hidden — OTel traces, Langfuse dashboard, pi session logs, confidence and completeness signals in state
- Principle 8: Use only reliable proven technology — fsspec not bespoke storage, LangGraph RetryPolicy not custom middleware, Langfuse not custom telemetry
- Principle 12: Genchi genbutsu — full session logs always available, artifacts retrievable with full provenance, summaries exist alongside full records never instead of them
- Principle 13: Nemawashi — slow consensus in planning (planner iterates with researcher and human to build an approved work brief), fast execution in production (deterministic graph execution once brief is approved)
- Principle 14: Hansei and kaizen — the observability layer exists to enable continuous improvement. Langfuse actuals compared against SLA descriptors over time. QA revision notes identify recurring failure patterns

**From A2A**
- Preserve opacity — agents collaborate without exposing internal memory, logic, or tooling
- The orchestrator's only opinion is that agents are blackboxes with interfaces

**Foundational decisions**
- One uniform interface per agent resolves the MxN contracts problem
- Agents are reusable capability units, not workflow-specific components
- Agent selection is fixed at graph construction time — LangGraph conditional edges are the routing mechanism
- No runtime discovery, no dynamic registry — the graph is the authoritative source for which agents exist and when they are invoked
- HITL is the orchestrator's concern, not the agent's — agents emit honest signals, the orchestrator acts on them
- All agent signals are informational — agents influence but do not control routing, QA invocation, or HITL

---

## Layer 1 — Agent

Each agent is a capability unit with a minimal base system prompt, an explicitly declared typed toolset, and a runtime of choice. The orchestrator is indifferent to the runtime. Pi is the recommended runtime for reference implementations. The agent has no knowledge of the graph, other agents, or the workflow it participates in.

### Agent SDK

The Agent SDK is a pip-installable Python package (~300 lines, no mandatory dependency on LangGraph or any orchestration framework) that provides the `@agent` decorator and supporting utilities. It is the adapter between agent capability logic and the orchestration layer. Non-Python agents implement the interface directly without the SDK.

**How it works**

The decorator uses Python's standard `contextvars.ContextVar` to set an `AgentRunContext` object before the decorated function executes. Any code inside the function — including nested calls, async calls, and calls to SDK utility functions — can access this context without it being passed as a parameter. This is the same mechanism Prefect uses for `get_run_context()`. The context is async-safe and task-isolated — concurrent invocations do not bleed into each other.

The decorator wraps the function in a try/except and always returns an `AgentResult` regardless of what happens inside. The orchestrator always receives a well-formed result. There is no code path that produces an unexpected shape.

**`@agent` decorator**

Wraps any Python callable. Registers the function as the handler for a specific agent ID and command. Parameters: `agent_id` (string, mandatory), `command` (string, optional, defaults to `"fast"`).

The decorator is structural metadata — it declares what capability this function implements and under what command name it can be invoked. Runtime context (task, context entries, trace ID, run ID, skills, command, effort) is injected into the function as arguments by name matching against `AgentRunContext` fields. The function declares only what it needs. Fields not declared as parameters are silently omitted. Fields declared that are not in `AgentRunContext` raise a clear error at decoration time, not at call time.

```python
# Minimal — "fast" is the default command, only declares what it needs
@agent(agent_id="researcher")
async def researcher(task: str):
    return await quick_search(task)

# Named command, uses context and wants command name for logging
@agent(agent_id="researcher", command="deep")
async def researcher_deep(task: str, context: list, command: str, effort: str):
    get_run_logger().info("invoked", command=command, effort=effort)
    return await deep_research(task, context, effort=effort)

# Domain-specific commands — same agent, different capabilities
@agent(agent_id="writer", command="translate")
async def writer_translate(task: str, context: list, effort: str):
    return await translate(task, context, effort=effort)

@agent(agent_id="analyst", command="ask")
async def analyst_ask(task: str):
    return await ad_hoc_query(task)

@agent(agent_id="analyst", command="deep-analysis")
async def analyst_deep(task: str, context: list, effort: str):
    return await multi_step_analysis(task, context, effort=effort)

# HTTP adapter to an external service in any language
@agent(agent_id="rust-analyst")
async def rust_analyst(task: str):
    response = await http_client.post("http://rust-service/run",
                                      json={"task": task})
    return response.json()["result"]
```

The same agent ID registered across multiple functions with different command names binds them as distinct capabilities of the same agent. The orchestrator routes to the correct function by agent ID and command name.

**Commands and calling conventions**

Commands are plain strings. The SDK defines two conventional command names that carry implied calling conventions: `"fast"` (synchronous, bounded, returns inline result) and `"deep"` (async, long-running, writes catalogue artifacts). These are defaults and conventions, not constraints. An agent that only has one calling convention can use a single command name for everything.

Domain-specific commands have no implied calling convention at the SDK level. Their calling convention — synchronous or async, inline result or catalogue artifacts — is declared in the capability descriptor and the node wrapper uses this to determine the right HTTP pattern at call time.

The `command` field is available for injection if the function declares it, useful for logging or passing downstream. It is always the registered command name for that function — it does not vary at call time.

**Effort — invocation-time concern**

Effort is not part of command registration. It is passed in the input envelope at call time by the orchestrator, expressing how much work the caller wants done for this particular invocation. The agent author reads `effort` from the injected context and decides what it means internally — fewer iterations, a lighter model, shallower search, a draft pass versus a thorough pass.

`effort` is optional with no fixed vocabulary. Sensible values are strings like `"low"`, `"high"`, `"draft"`, `"thorough"` — whatever the agent author documents in their capability descriptor. An agent that does not meaningfully vary its approach ignores the field entirely. The orchestrator passes effort at invocation time based on what the graph needs at that point — a targeted replan calls the planner with `effort="low"`, a full brief production calls it with `effort="high"`.

```python
@agent(agent_id="planner", command="plan")
async def planner(task: str, context: list, effort: str = "high"):
    if effort == "low":
        return await quick_replan(task, context)
    return await full_plan(task, context)
```

**Automatic content offload**

When a function returns a value that exceeds the configured content limit, the decorator automatically writes the full content to the catalogue and returns a pointer in the output envelope. This happens transparently for any mode. A naive implementation that returns a large string gets correct behaviour without any explicit `write_artifact()` call — the developer does not need to think about content limits for simple cases and can opt into explicit artifact management via `write_artifact()` when they need multiple named artifacts with summaries, confidence scores, and labels.

**`AgentResult`**

The wrapped result object. Never constructed manually by the function author. The LangGraph node wrapper translates it to the output envelope before updating graph state.

| Field | Description |
|---|---|
| `success` | Boolean — did the agent complete without a semantic error |
| `output` | The function's return value after size handling (inline result or catalogue pointer) |
| `artifacts` | List of `ArtifactPointer` collected from `write_artifact()` calls. Preserved even when a typed exception is raised |
| `signals` | `AgentSignals` object populated from typed exception raises |
| `trace_id` | Echoed from input envelope |
| `run_id` | Echoed from input envelope |

**`AgentRunContext`**

Available via `get_run_context()` anywhere inside a decorated function. Also the source for automatic parameter injection — the decorator inspects the function signature at decoration time using `inspect.signature()` and injects matching fields by name at call time.

| Field | Description |
|---|---|
| `task` | Natural language instruction from input envelope |
| `context` | Typed context entry list from input envelope |
| `command` | The command name this function was registered for (e.g. `"fast"`, `"deep"`, `"translate"`, `"ask"`) |
| `effort` | Optional invocation-time effort hint from the orchestrator (e.g. `"low"`, `"high"`). Absent if not passed |
| `trace_id` | OTel trace ID |
| `run_id` | LangGraph run ID |
| `agent_id` | The agent's registered ID |
| `skills` | List of skill identifiers loaded for this invocation |

Any field can be declared as a function parameter to receive it. Fields not declared are simply not injected — no error, no boilerplate. A function that declares `command` receives it for logging or passing downstream. A function that declares `effort` receives it for internal branching. A function that declares neither does not receive them. The decorator does not impose unused arguments on the function author.

`ctx.context` is strongly typed at the envelope level (each entry has `type`, `summary`, `url`, `content_type`) but the payload behind a `url` is intentionally opaque. The agent inspects entries by type, decides from the summary whether to fetch, and interprets the bytes according to `content_type`. No agent-to-agent content contracts exist — this is the blackbox principle applied to context.

**SDK utility functions**

`get_run_context()` — returns `AgentRunContext` from the ContextVar. Returns a safe default outside the decorator so functions remain testable without orchestration infrastructure.

`get_run_logger()` — returns a structured logger pre-populated with `trace_id`, `run_id`, `agent_id`, and `command`. A convenience wrapper over `get_run_context()` plus the OTel SDK. Agents that want full control use the OTel SDK directly with IDs from context. A no-op logger is returned outside the decorator.

`write_artifact(content, content_type, summary, confidence, completeness, sensitivity_label, ...)` — writes bytes to the catalogue HTTP API using the endpoint from environment config and IDs from `AgentRunContext`. Returns an `ArtifactPointer` (ID and URL). The decorator's `finally` block collects all pointers regardless of whether the function succeeded or raised, preserving partial output on exception.

**Typed exceptions**

Raised by the function author. Caught by the decorator. Translated into `AgentResult.signals`. The function never constructs the signals dict manually.

| Exception | Signal set | Notes |
|---|---|---|
| `NeedsHumanReview(reason="...")` | `signals.needs_human_review = true` | Partial artifacts already written are preserved in `AgentResult.artifacts` |
| `EscalationRequired(reason="...")` | `signals.escalation_requested = true` | May trigger HITL if a human with appropriate permissions must act |
| `SemanticError(type=..., message="...")` | `signals.semantic_error` populated | Soft failure — no results, irreconcilable conflict, quality below recoverable threshold |

Unexpected exceptions are caught and wrapped as `SemanticError(type="unexpected_error")`. Infrastructure exceptions never propagate to crash the LangGraph node.

**SDK exports (complete list)**

`@agent`, `AgentResult`, `AgentRunContext`, `get_run_context()`, `get_run_logger()`, `write_artifact()`, `emit_progress()`, `NeedsHumanReview`, `EscalationRequired`, `SemanticError`

### Streaming — Granularity Tradeoff and Illustrative Patterns

Streaming granularity is a tradeoff the agent developer works around. The architecture is not prescriptive about how agents implement streaming internally. The following are illustrative patterns showing how different agent types can optionally provide intra-node progress events. None of these are requirements.

LangGraph nodes emit two categories of events that all agents get for free: a node start event and a node completion event with the state delta. Anything richer than this is opt-in and agent-specific.

For Python SDK agents that want to emit intra-node progress, LangGraph provides `get_stream_writer()` — a context-local writer available inside any function running within the LangGraph execution thread. The SDK's `emit_progress()` is a thin wrapper over this. It is a no-op outside the decorator context so functions remain testable without orchestration infrastructure. Python 3.11 or above is required for `get_stream_writer()` to propagate correctly across async tasks.

**Python SDK agent — optional progress emission**

A Python agent that wants to emit progress calls `emit_progress()` at whatever granularity makes sense. The chat layer receives these as `stream_mode="custom"` events interleaved with the standard `stream_mode="updates"` node completion events.

```python
@agent(agent_id="researcher", command="deep")
async def researcher(task: str, context: list, effort: str = "high"):
    sources = await gather_sources(task, depth=effort)
    results = []
    for i, source in enumerate(sources):
        result = await process(source)
        results.append(result)
        # optional — developer chooses whether and how often to emit
        emit_progress({"searched": i + 1, "total": len(sources)})
    return synthesise(results)
```

**CLI agent — optional stdout capture**

A CLI compiled from any language writes progress events as newline-delimited JSON to stdout. The Python wrapper function that spawns the subprocess reads stdout in a loop and forwards progress events via `emit_progress()`. The CLI itself has no SDK dependency — it writes to stdout, which is its natural output channel.

```python
@agent(agent_id="rust-analyst", command="deep-analysis")
async def rust_analyst(task: str, run_id: str, effort: str = "high"):
    proc = await asyncio.create_subprocess_exec(
        "./analyst", "--task", task, "--run-id", run_id, "--effort", effort,
        stdout=asyncio.subprocess.PIPE
    )
    result_line = None
    async for line in proc.stdout:
        event = json.loads(line)
        if event["type"] == "progress":
            emit_progress(event)          # optional forwarding
        elif event["type"] == "result":
            result_line = event
    await proc.wait()
    return result_line["output"]
```

The CLI developer chooses whether to emit progress at all. The wrapper developer chooses whether to forward it. Both decisions are independent.

**HTTP agent — optional async result with pull streaming**

For HTTP agents doing long-running work, holding an HTTP connection open for many minutes is fragile. An agent can optionally implement an async response pattern — return 202 Accepted with a task ID immediately, then expose its own status and stream endpoints. The node wrapper polls or subscribes to SSE and forwards events via `emit_progress()`.

```python
@agent(agent_id="deep-researcher", command="deep")
async def deep_researcher(task: str, run_id: str, trace_id: str, effort: str = "high"):
    response = await http_client.post(
        "http://researcher-service/deep",
        headers={"traceparent": trace_id},
        json={"task": task, "run_id": run_id, "effort": effort}
    )

    if response.status_code == 202:
        # agent supports async result — pull progress and wait for completion
        task_id = response.json()["task_id"]
        stream_url = response.json().get("stream_url")

        if stream_url:
            # agent exposes SSE stream — subscribe and forward
            async with http_client.stream("GET", stream_url) as stream:
                async for line in stream.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    if event["type"] == "progress":
                        emit_progress(event)     # optional forwarding
                    elif event["type"] == "result":
                        return event["output"]
        else:
            # agent supports polling only
            while True:
                status = await http_client.get(
                    f"http://researcher-service/status/{task_id}"
                )
                data = status.json()
                if data.get("progress"):
                    emit_progress(data["progress"])  # optional forwarding
                if data["status"] == "complete":
                    return data["output"]
                await asyncio.sleep(5)
    else:
        # agent is synchronous — result is in the response body
        return response.json()["output"]
```

The HTTP agent service hosts its own `/status` and `/stream` endpoints. The architecture has no hosting responsibility for these. The agent developer implements them alongside their `/deep` endpoint if they want to support richer progress visibility. Agents that do not implement them simply return 200 with the result when the work is done — the node wrapper handles both cases.

The dependency direction is clean in all cases. The orchestrator reaches into the agent when it wants information. The agent never makes outbound calls to the orchestrator. There is no side channel.

### Internal Customisation

**Skills** are versioned markdown files providing domain knowledge, working patterns, and behavioural guidance loaded into agent context at invocation time. They follow Claude's approach — composable, hierarchical from global to domain to invocation-specific, stored as plain text files in the skill store, referenced by name in the work brief's capability requirements section.

**Extensions** are lifecycle hooks following pi's pattern that customise agent behaviour without touching the base runtime. Hook points: `context`, `tool_call`, `before_agent_start`, `session_before_compact`, `session_start`. Reference extensions:

| Extension | Purpose | Typical use |
|---|---|---|
| Thinking | Structured pre-commitment reasoning before output | Long-running commands, QA, planning |
| Todo | Multi-step work tracking within a single invocation | Research, writing, multi-step analysis |
| Context compression | Long session management, summarises older turns | Any long-running async command |
| Tool result size | Intercepts large tool results, offloads to catalogue before context bloat | Research, data analysis |

Extensions are intra-agent concerns — they operate inside the agent's loop and are invisible to the orchestrator. The `@agent` decorator operates at the agent boundary. They are parallel systems at different scopes and are not coupled.

### Agent Interface

Every agent exposes one or more named command endpoints. The command name is part of the route — `/agents/{agent_id}/{command}`. There is no fixed set of commands every agent must implement. Agent developers define the commands that match their capability.

Two conventional commands carry implied calling conventions that the orchestrator and SDK understand natively:

**`fast`** — synchronous, bounded effort, returns an inline result directly. Suitable for quick questions, ad hoc queries, targeted replanning, single bounded tasks. The default command when none is specified.

**`deep`** — async, long-running, writes one or more artifacts to the catalogue and returns pointers and summaries. Suitable for exhaustive research, full document production, comprehensive analysis.

Agents are not required to implement both. A specialised agent might only expose domain-specific commands. A simple agent might expose only `fast`. The calling convention for any non-conventional command — synchronous or async, inline result or catalogue artifacts — is declared in the capability descriptor.

Status and cancellation are LangGraph run-level concerns exposed by the FastAPI server, not agent-level endpoints.

### Input Envelope

Universal across all agents and all commands.

| Field | Type | Required | Description |
|---|---|---|---|
| `task` | string | mandatory | Natural language instruction |
| `context` | list of typed entries | optional | Artifact pointers, instructions, constraints, skill references |
| `command` | string | optional | Which command to invoke. Defaults to `"fast"` if omitted |
| `effort` | string | optional | Invocation-time effort hint. No fixed vocabulary — agent author documents what values they support. Absent means the agent uses its own default |
| `trace_id` | string | mandatory | OTel trace ID for continuity |
| `run_id` | string | mandatory | LangGraph run ID for correlation |

Each context entry has: `type`, `summary` or inline `content` for small entries, and a `url` for catalogue-backed entries. The URL is a standard HTTP address returning artifact bytes. The metadata sidecar is at the same URL with a standard suffix. No client library required. Context entry types: `artifact`, `work_brief`, `constraint`, `instruction`, `skill_reference`.

For Python agents using the SDK, any `AgentRunContext` field declared as a function parameter is injected automatically by the decorator via name matching. Fields not declared are silently omitted. All fields are also available via `get_run_context()` regardless of whether they are declared as parameters.

### Output Envelope — synchronous commands

Returned directly as the HTTP response body. Used for commands with a synchronous calling convention (typically `fast` and any other bounded commands).

| Field | Type | Required | Description |
|---|---|---|---|
| `result` | string | mandatory | Bounded length answer or content |
| `confidence` | float 0–1 | mandatory | Agent-declared confidence |
| `signals` | object | mandatory | See Signals section |
| `trace_id` | string | mandatory | Echoed |
| `run_id` | string | mandatory | Echoed |

### Output Envelope — async commands

Used for commands with an async calling convention (typically `deep` and other long-running commands). The agent writes one or more artifacts to the catalogue and returns pointers.

| Field | Type | Required | Description |
|---|---|---|---|
| `artifacts` | list | mandatory | One or more artifact entries |
| `signals` | object | mandatory | See Signals section |
| `trace_id` | string | mandatory | Echoed |
| `run_id` | string | mandatory | Echoed |

Each artifact entry carries: `artifact_id`, `url`, `summary` (strongly recommended), `confidence`, `completeness` (complete / partial / resource-bounded), `content_type`, and an optional `label`.

For Python agents using the SDK, `AgentResult` is the internal representation. The LangGraph node wrapper translates it to this envelope before updating graph state.

### Signals

Present on both output envelopes. The orchestrator reads these signals and acts on them according to its own policy. Agents emit honest structured information. The orchestrator decides what action each signal triggers.

| Field | Type | Description |
|---|---|---|
| `needs_human_review` | boolean | When true, orchestrator calls LangGraph `interrupt()` surfacing to a human |
| `review_reason` | string (optional) | What the human needs to assess or decide |
| `escalation_requested` | boolean | Agent has hit a capability or permissions boundary |
| `escalation_reason` | string (optional) | What capability or permission is needed. May also trigger HITL if a human with appropriate permissions must act |
| `revision_notes` | object (optional) | Structured feedback for replanning or redrafting by the next invocation |
| `semantic_error` | object (optional) | Soft failures — no results found, irreconcilable conflict, quality below recoverable threshold. Carries a type enum and message string |

`needs_human_review` is a signal, not a gate. The orchestrator applies its own policy rules independently. Agents can request review but cannot suppress it. For Python SDK agents, signals are populated by raising typed exceptions — the decorator catches them and sets the corresponding fields.

---

## Layer 2 — Artifact Catalogue

A thin FastAPI service (~200 lines) wrapping fsspec for object storage and SQLAlchemy for the metadata index. Binary content and a `meta.json` sidecar are written to the object store at predictable paths under a generated artifact ID. The metadata index holds all structured fields for querying.

| Environment | Object store | Index |
|---|---|---|
| Local development | Filesystem | SQLite |
| Production | S3 / GCS / any fsspec backend | Postgres |

The switch between environments is pure environment configuration. No code changes.

Artifacts are addressed by URL. Standard HTTP GET returns artifact bytes. A standard URL suffix returns the metadata sidecar. No client library required — any HTTP client in any language suffices.

For Python agents using the SDK, `write_artifact()` is the interface to the catalogue. It reads the endpoint from environment config, injects `trace_id` and `run_id` from `AgentRunContext`, and returns an `ArtifactPointer`. Agents in other languages call the HTTP API directly with the same mandatory fields.

### Artifact Metadata Schema

| Field | Required | Description |
|---|---|---|
| `artifact_id` | mandatory | Unique identifier generated at write time |
| `content_type` | mandatory | MIME type |
| `content_length` | mandatory | Byte size, derived at write time |
| `content_encoding` | optional | Present if compressed |
| `content_hash` | mandatory | Checksum for integrity verification |
| `summary` | strongly recommended | Bounded length text summary. Primary mechanism for orchestrator routing decisions without fetching full content |
| `schema_version` | mandatory | Version of the artifact schema |
| `created_by` | mandatory | Agent name and version |
| `created_at` | mandatory | Timestamp |
| `trace_id` | mandatory | OTel trace ID |
| `run_id` | mandatory | LangGraph run ID |
| `invocation_command` | mandatory | Command name that produced this artifact |
| `invocation_effort` | optional | Effort level at the time of invocation |
| `confidence` | mandatory | Numeric 0–1 |
| `completeness` | mandatory | complete / partial / resource-bounded |
| `sensitivity_label` | mandatory | public / internal / confidential / restricted |
| `data_residency` | mandatory | Permitted storage and processing jurisdiction |
| `retention_policy` | mandatory if PII | Duration or expiry timestamp |
| `pii_flag` | mandatory | Boolean, whether artifact contains PII |
| `tags` | optional | Free-form key-value pairs for extension without schema changes |

**Write-time invariants** enforced by the service: all mandatory fields present, sensitivity label is a valid enum value, if `pii_flag` is true then `retention_policy` must be set, content hash computed and stored. Any write missing mandatory fields is rejected.

---

## Layer 3 — Agent Capability Descriptors

Static typed configuration loaded at startup. Not a runtime service. Not queried dynamically. Each agent has a descriptor defining:

- Capability description
- Registered commands with their calling convention (synchronous or async), expected effort vocabulary, and SLA characteristics per command: expected latency envelope, cost tier, model selection
- Confidence model
- Retry semantics

The calling convention for each command determines how the node wrapper calls the agent — synchronous request-response, or async 202 with polling or SSE. The conventional commands `fast` and `deep` have their calling conventions implied by name. All other commands declare their calling convention explicitly in the descriptor.

Descriptors serve three purposes: human documentation, LangGraph `RetryPolicy` configuration per node, and comparison of actuals captured in Langfuse against declared SLA characteristics over time (kaizen input).

The graph is the authoritative source for which agents exist and when they are invoked. Descriptors describe capability and inform policy, not routing.

---

## Layer 4 — Orchestration

A LangGraph StateGraph whose nodes are thin wrappers around agent interface calls. The graph owns all routing, branching, iteration, and parallelism decisions via conditional edges. Agent selection is fixed at graph construction time.

### Node Wrapper

Each LangGraph node calls the agent (directly as a Python function in the co-located deployment, or via HTTP when distributed) and receives either an `AgentResult` (Python SDK agents) or a raw output envelope (non-Python agents). The node wrapper:

- Starts an OTel span with agent ID, command, effort, run ID, and sensitivity label as attributes
- Injects the W3C `traceparent` header into outbound HTTP calls for distributed agents
- Receives the result and translates `AgentResult` to the output envelope if needed
- Calls `enforce_content_limit` if the summary exceeds the configured limit
- Reads signals and calls `interrupt()` if `needs_human_review` is true or policy requires it
- Updates lean LangGraph state

The node wrapper has no knowledge of the agent's internal runtime, SDK usage, or extensions. It sees only the output envelope interface.

### Graph State

LangGraph state entries are always lean. Full artifact content never lives in graph state.

| Field | Description |
|---|---|
| Artifact URL and summary | Pointer to catalogue content plus bounded summary |
| Confidence | From agent output envelope |
| Completeness | From agent output envelope |
| Signals | From agent output envelope |
| Trace ID | For OTel continuity |
| Run ID | For LangGraph state continuity |
| Agent ID | Which agent produced this entry |
| Command | Which command was invoked |
| Effort | Effort level passed at invocation time |

### Content Limit Enforcement

The `enforce_content_limit` helper is called by the node wrapper after each agent response. If the summary exceeds the configured limit it writes the full content to the catalogue, generates a trimmed summary, and replaces the state entry with a pointer and bounded summary. This is the only custom cross-cutting concern not handled by LangGraph natively.

### Retry and Timeout

Handled by LangGraph's `RetryPolicy` configured per node from the agent's capability descriptor. No custom retry middleware. `SemanticError` with `type="unexpected_error"` triggers retry if the descriptor declares it retryable.

### Durable Execution — Boundaries and Responsibilities

LangGraph's checkpointer provides inter-node durability. It guarantees that a completed node's output survives an orchestrator process crash, and that graph execution resumes from the last successfully completed node. This is the orchestrator's durability guarantee and it is unconditional.

What the checkpointer does not provide is intra-node durability. If an agent is mid-way through a long `/deep` invocation when its process crashes, the node has not yet completed and there is nothing for the checkpointer to resume from. The orchestrator's `RetryPolicy` fires, the node is retried, and the agent starts from scratch. For short invocations this is acceptable. For a researcher doing hundreds of parallel searches over twenty minutes, starting from scratch is a significant cost.

Intra-invocation durability is the agent's responsibility, not the orchestrator's. An agent that cannot afford to lose internal progress implements its own durability strategy. Temporal is the natural choice for complex parallel work — it provides Activity-level durability so a researcher that completes 150 of 200 searches and then crashes resumes from search 151, not search 1. Pi's session persistence provides lighter sequential durability for agents whose internal state is a conversation history. A simple agent doing bounded synchronous work needs neither.

The orchestrator is indifferent to which strategy an agent uses internally. The `RetryPolicy` fires the same way regardless — it retries failed nodes. What differs is what "retry" costs: for a Temporal-backed agent it resumes cheaply from the last completed activity; for an agent without internal durability it restarts from zero. This is an agent capability decision documented in the capability descriptor's retry semantics, not an orchestration concern.

The two durability mechanisms are complementary at different granularities. LangGraph guarantees the graph survives orchestrator failures. Each agent is responsible for surviving its own internal failures during a single invocation.

**LangGraph retry as Temporal resume**

Because `run_id` and `trace_id` are first-class injected fields in `AgentRunContext`, a Temporal-backed agent can use them to make LangGraph retries transparently resume an existing Temporal workflow rather than starting a new one:

```python
@agent(agent_id="researcher", mode=Mode.DEEP)
async def researcher_deep(task: str, run_id: str, trace_id: str):
    workflow_id = f"research-{run_id}"  # deterministic from LangGraph run ID

    result = await temporal_client.execute_workflow(
        ResearchWorkflow.run,
        task,
        id=workflow_id,
        task_queue="research-queue",
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE
    )
    return result
```

The `workflow_id` is deterministically derived from `run_id`. If LangGraph retries this node the decorator injects the same `run_id`. The same `workflow_id` is computed. Temporal's `ALLOW_DUPLICATE` reuse policy returns the existing running workflow, the completed result, or starts a new execution if the previous one failed — whichever applies. From LangGraph's perspective the node was retried and eventually returned a result. From Temporal's perspective execution may have been resumed rather than restarted. The two systems never coordinate directly. The `run_id` is the shared idempotency key across the boundary.

The `trace_id` is passed as a Temporal search attribute so the workflow's activities appear as child spans in the same Langfuse trace. The full picture — graph routing in LangGraph, workflow activities in Temporal, tool calls inside activities — is one coherent trace.

**The same pattern applies to any external service**

An HTTP adapter to a service in any language follows the same model. The function receives `run_id` and `trace_id` as injected arguments and passes them as correlation headers. The external service uses the run ID for its own idempotency — if the node is retried with the same run ID, the service can return a cached result rather than recomputing. The function translates the service's response into a return value or a typed exception. Signals flow to the orchestrator through the typed exception mechanism, not through any direct coupling:

```python
@agent(agent_id="rust-analyst", mode=Mode.DEEP)
async def rust_analyst(task: str, run_id: str, trace_id: str):
    response = await http_client.post(
        "http://rust-service/run",
        headers={"x-run-id": run_id, "traceparent": trace_id},
        json={"task": task}
    )
    data = response.json()
    if data.get("needs_review"):
        raise NeedsHumanReview(reason=data["review_reason"])
    return data["result"]
```

The Rust service never knows it is talking to LangGraph. The orchestrator never knows the agent is backed by a Rust service. The injected fields connect the agent to the orchestration context. The typed exceptions connect the agent to the orchestration signals. Both connections are one-directional. The orchestrator's behaviour is driven entirely by the output envelope and signals — never by knowledge of the agent's internals.

### Human-in-the-Loop

HITL is entirely the orchestrator's concern, implemented in two places:

**Structural checkpoints** — nodes that always require human review use `interrupt_before` at graph compile time. No agent signal required. Example: the publisher node always pauses before execution.

**Policy-driven checkpoints** — nodes where review depends on output signals use `interrupt()` called inside the node wrapper after evaluating signals. `needs_human_review: true` always triggers `interrupt()`. `escalation_requested: true` triggers routing logic that may also trigger `interrupt()` if the escalation requires human action.

The orchestrator's policy layer runs independently — certain output types always route to QA or HITL regardless of what the agent signals.

Agents that have their own internal HITL logic surface the outcome via `needs_human_review: true` in the output envelope (or by raising `NeedsHumanReview` in the SDK). This is the adapter between any agent's internal review logic and LangGraph's interrupt system. The orchestrator handles the LangGraph interrupt. The agent's internals remain opaque.

### QA as Structural Safeguard

The QA agent is independent of the producing agent. Producing agent confidence and signals inform how deeply QA evaluates, not whether it evaluates. QA invocation is an orchestrator policy decision. No agent can bypass QA by suppressing signals.

### Reflection and Replanning

Conditional edges read confidence, completeness, and `semantic_error` signals from state. `revision_notes` from the output envelope flow back into the next agent invocation's context list as a typed `instruction` entry. Maximum iteration counts are set as conditional edge exit conditions following LangGraph's draft-execute-revise pattern.

### Planning and Work Brief

The planner is the translation layer between vague user intent and structured work briefs. It never passes raw user input to downstream agents. Every draft work brief is presented to the human for approval before any production work begins. Assumptions the planner made in translating user intent are surfaced explicitly at this checkpoint (nemawashi — slow consensus before fast execution).

**Work brief — universal layer** (all work briefs regardless of domain):

| Field | Description |
|---|---|
| Goal | Single clear outcome statement |
| In scope | What is explicitly included |
| Out of scope | What is explicitly excluded |
| Quality criteria | What good looks like — the agent's termination condition |
| Constraints | Time, length, format, cost, audience, sensitivity |
| Capability requirements | Which agents, which commands, which effort level, which skills to load per agent |
| Human checkpoint policy | Where explicit human approval is required and what happens on rejection |
| Assumptions | Every significant interpretive decision the planner made in translating user intent |

**Work brief — specialist layer** (populated for domain-specific work):

| Field | Description |
|---|---|
| Domain context | Background the agent cannot be expected to know from base training or skills |
| Evaluation methodology | Prescriptive methodology rather than descriptive quality criteria |
| Output schema | Specific structured format the agent must produce |
| Acceptance tests | Concrete verifiable conditions for sufficiently specialised work |

The work brief is a catalogue artifact. Its summary is the most important summary in the system — it informs every subsequent routing decision.

### Checkpointing

The LangGraph checkpointer writes to Postgres from day one. The orchestrator is stateless and restartable. In-flight executions survive process restarts.

### Reference Agents

Five reference agents implemented using pi as the runtime, all decorated with `@agent`.

| Agent | Core capability | Commands | Key extensions | HITL |
|---|---|---|---|---|
| Planner | Translates user intent to structured work briefs, decomposes tasks | `fast`, `plan` | Thinking, Todo | Draft brief approval always |
| Researcher | Information gathering across any domain to specified depth | `fast`, `deep` | Todo, Context compression, Tool result size | Policy-driven on low confidence |
| Writer | Structured content production in any domain and format | `fast`, `deep`, `translate` | Todo, Context compression | Policy-driven via QA |
| QA | Evaluates content against work brief quality rubric | `fast`, `deep` | Thinking, Todo | When confidence below threshold |
| Publisher | Format transformation and platform optimisation | `plan`, `publish` | Todo | Always, before any publication artifact is produced |

Domain specialisation comes from skills loaded at invocation time from the work brief capability requirements. Behavioural depth comes from extensions loaded based on command and task complexity. Effort is passed by the orchestrator at invocation time. The base agent is thin — skills, extensions, and effort handling carry the domain weight.

---

## Layer 5 — Observability

OpenTelemetry is the contract between agents and the observability system. Agents emit spans using the standard OTel SDK and `gen_ai.*` semantic conventions. They have no opinion about where spans go. The backend is pure environment configuration — a single `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable pointing at the self-hosted Langfuse instance.

### Instrumentation Levels

**Intra-agent** — spans fire from within the agent runtime via pi's extension hooks or equivalent: tool calls, model calls, context events. For Python SDK agents, `get_run_logger()` emits structured spans pre-populated with run context. Captured per agent, invisible to the orchestrator.

**Inter-agent** — spans fire from the LangGraph node wrapper: invocation started, completed, failed. Applied uniformly across all agents regardless of runtime.

### Trace Continuity

W3C `traceparent` headers are propagated by the node wrapper across service boundaries ensuring a single coherent trace in Langfuse for an entire graph execution.

In the co-located single-server deployment, OTel context propagation is automatic across Python async tasks. The `AgentRunContext` ContextVar and the OTel context are both in scope throughout the decorated function's execution including any nested async calls.

When agents become separate services, the node wrapper injects `traceparent` explicitly before the HTTP call. The agent's FastAPI endpoint extracts and activates it before setting the `AgentRunContext` ContextVar, ensuring the full trace from graph node through agent internals appears as a single coherent trace in Langfuse.

### Langfuse

Self-hosted. Receives OTel traces via OTLP over HTTP. Extended with additional span attributes: agent ID, run ID, command, effort, sensitivity label, and signals from the output envelope.

Pi session logs provide the full intra-agent record independently of OTel — the genchi genbutsu layer for when a trace shows something unexpected.

Cost and latency are captured in OTel traces. They are not duplicated in the output envelope. Actuals are compared against declared SLA characteristics in capability descriptors over time (kaizen input).

---

## Layer 6 — Transport

Initially a single FastAPI server hosting all agents and the orchestrator as co-located services. The agent interface is transport-agnostic — each agent is a callable invoked as a direct Python function or over HTTP depending on a config flag. When an agent needs independent scaling it moves to its own service and only the environment config changes. The interface, the graph, and agent internals are untouched.

`/fast` invocations are synchronous. `/deep` invocations are async — the node wrapper calls the agent and awaits the output envelope. LangGraph's async node support and Postgres checkpointer handle long-running graph executions and resumption after interrupts.

When a Python SDK agent moves to a separate service, its FastAPI endpoint: extracts the OTel `traceparent` header and activates it as the OTel context, sets the `AgentRunContext` ContextVar from the envelope fields, then calls the decorated function directly. The decorator behaves identically to the co-located case. The separation is invisible to both the decorator and the node wrapper.

Shared infrastructure — artifact catalogue, any future shared service — is an HTTP API. Service location and credentials are environment variables following a consistent naming convention. No shared client libraries required. Standard HTTP clients in any language suffice.

---

## Remaining Open Items

| Item | Description |
|---|---|
| Parent graph topology | Node and edge structure for the two workflow phases, structural HITL checkpoint placement, async section production loop model |
| Skill store structure | Directory layout, naming conventions, how skills are referenced in work brief capability requirements, how they are loaded into agent context at invocation time |
| Extension interface specification | Precise hook signatures for the four reference extensions following pi's pattern |
| Base agent definitions | Base system prompt, toolset, default extension stack per mode, and SLA metadata for each of the five reference agents |

---

## Design Principles in Practice

This section examines how the founding principles from Mario Zechner's pi-agent philosophy and Toyota's production system manifest in this architecture, and where they point for future iterations.

### Mario's Principles — Current Alignment and Future Application

**Build only what you need**

Current alignment: The artifact catalogue is ~200 lines wrapping fsspec and SQLAlchemy rather than adopting MLflow. The SDK is ~300 lines with no framework dependencies. The middleware layer was designed and then largely eliminated when LangGraph's `RetryPolicy` was found to cover the same ground. The dynamic agent registry was designed and then eliminated when fixed graph construction was recognised as sufficient.

Future application: As the system grows, the pressure to add features will increase. The right response to each new capability request is the same question — what is the minimum that enables this? The skill store should remain a directory of markdown files for as long as that is sufficient. The agent descriptor should remain a static config for as long as agents are not dynamically discovered. Every abstraction should justify itself against a concrete use case before it is built.

**Full observability is non-negotiable**

Current alignment: OTel spans at two levels (intra-agent via pi extensions and inter-agent via node wrapper). Pi session logs as ground truth independent of OTel. Langfuse as the surface. Artifact provenance traceable to agent, run, and trace. Confidence, completeness, and signals in graph state so the orchestrator's decisions are always explainable.

Future application: The most important gap is the absence of a policy for acting on observability data. Langfuse will accumulate actuals — latency, token consumption, confidence distributions, QA pass rates by agent and skill. The kaizen loop requires a defined process for reviewing this data and updating SLA descriptors, skill files, and system prompts. This is an operational design question, not a technical one, but it needs to be answered before the system reaches production at scale. Without it, the observability layer is a dashboard rather than a learning system.

**Minimal stable interfaces**

Current alignment: Two agent endpoints (`/fast`, `/deep`). One input envelope schema. Two output envelope schemas. One artifact metadata schema. One signals schema. Eight SDK exports. The LangGraph node wrapper sees only `AgentResult`. The orchestrator sees only the output envelope. These interfaces are designed to not change.

Future application: The most likely pressure point is the context entry list. As new agent types emerge with more complex context requirements, there will be pressure to add new entry types or new fields. The `tags` field on the artifact schema and the `type` field on context entries are the designated extension points — new requirements should be satisfied through these before the schema is versioned. Schema versioning (already in the artifact metadata via `schema_version`) is the right mechanism when a breaking change is unavoidable.

**Context engineering is the real work**

Current alignment: Content length limits prevent context bloat in graph state. The split between inline result and catalogue artifact means the orchestrator only carries summaries. Agents fetch full artifacts from the catalogue only when their capability requires it. The tool result size extension offloads large tool returns before they reach the LLM context. Skills are loaded hierarchically so agents only receive domain context relevant to the current invocation.

Future application: The biggest unsolved context engineering problem is across a long-running `/deep` invocation with many tool calls. The context compression extension manages this at the session level, but there is no systematic policy for what a researcher with 200 sources should load into context at what point. This is an area where the extension system's `context` hook — which can rewrite messages before the LLM sees them — is the right tool, and where careful experimentation with specific agent types will be required.

**Composability through layering**

Current alignment: Six layers, each ignorant of the others' internals. The SDK has no LangGraph dependency. The orchestrator has no knowledge of agent runtimes. The catalogue has no knowledge of agents or workflows. Pi extensions operate inside the agent loop and are invisible to the decorator. The decorator operates at the agent boundary and is invisible to the node wrapper.

Future application: The composability principle is the strongest defence against the most common failure mode in systems like this — accumulating coupling as features are added. The test for any new feature is: which layer does it belong to, and does adding it require any other layer to know about it? If the answer is yes, the feature is in the wrong place. The layering discipline should be enforced as a review criterion, not just as an architectural aspiration.

**Sessions as first-class serialisable artifacts**

Current alignment: Pi sessions are JSONL, always persisted, always inspectable. LangGraph state is persisted to Postgres via the checkpointer. Artifacts are stored in the catalogue with full provenance. The entire execution history of any run is reconstructable from these three stores.

Future application: Session serialisation enables a capability not yet designed — replay and branching. A run that went wrong can be replayed from any checkpoint with different parameters. A draft work brief can be branched — two planning paths explored from the same starting point. These are natural consequences of first-class serialisation that become available without additional infrastructure, once the need for them becomes concrete.

---

### Toyota Way Principles — Current Alignment and Future Application

**Principle 1: Long-term philosophy**

Current alignment: Co-located deployment to start, with the seam for distribution already designed. SQLite locally, Postgres in production — same code, different config. The agent interface is transport-agnostic so moving agents to separate services requires no interface changes. These are deliberate decisions to not optimise prematurely while preserving the ability to scale when needed.

Future application: The most important long-term decision not yet made is the RBAC and access control model. It was explicitly deferred as out of scope. The sensitivity label, data residency, PII flag, and retention policy fields in the artifact schema are the foundation for that future work. When access control becomes necessary, the data is already there. The enforcement layer is additive. This is long-term philosophy expressed as restraint — the field is planted, the harvest is deferred.

**Principle 2: Continuous flow**

Current alignment: The assembly line metaphor for the workflow phases. Artifacts pass between agents rather than being batched. The async section production loop in the writing workflow means sections flow to QA as they are completed rather than waiting for all sections to finish. The pull model means work flows to agents when the graph needs it.

Future application: The most significant flow interruption in the current design is the planning phase approval checkpoint. The human must approve the work brief before production begins. This is correct from a quality perspective (nemawashi) but it is a batch point in the flow. A future iteration might explore progressive brief refinement — production begins on settled sections of the brief while unsettled sections continue to be refined — reducing the gap between planning and execution without sacrificing quality.

**Principle 3: Pull not push**

Current alignment: LangGraph's conditional edge model is inherently pull-based. Nodes execute when the graph routes to them. Agents are invoked on demand. The `/deep` async pattern means the orchestrator polls for results rather than blocking. No agent pre-computes output speculatively.

Future application: Pull systems depend on accurate demand signalling. The orchestrator's routing decisions are the demand signals. The quality of those signals — confidence scores, completeness flags, semantic errors — determines how well the pull system flows. Investing in the richness and accuracy of these signals over time is the kaizen work for the pull system.

**Principle 5: Jidoka**

Current alignment: Write-time invariants in the artifact catalogue reject malformed artifacts at the boundary. The node wrapper validates output envelopes before updating graph state. The QA agent is a structural safeguard, not an optional step. The `@agent` decorator catches unexpected exceptions before they crash the node. Each of these is a quality gate built into the system.

Future application: The weakest jidoka point in the current design is confidence score reliability. Agents self-declare confidence, which carries the self-assessment risk discussed during design. The longer-term jidoka improvement is calibration — comparing agent-declared confidence against QA outcomes over time (available in Langfuse) and feeding calibration data back into agent prompts and skill files. A miscalibrated agent that consistently declares high confidence on outputs that QA rejects is a defect in the quality gate, detectable through the observability layer.

**Principle 6: Standardisation**

Current alignment: The uniform agent interface is the primary standardisation. The artifact metadata schema is the secondary standardisation. The output envelope signals schema is the third. These three standards are the foundation on which composability, mixed runtimes, and future agent additions are possible.

Future application: The skill file format is not yet standardised beyond "markdown files." A more structured skill schema — with defined sections for domain context, working methodology, output format expectations, and quality criteria — would make skills more composable and easier to evaluate. This is a future standardisation opportunity that should be deferred until there are enough skills to identify the natural structure empirically rather than designing it speculatively.

**Principle 7: Visual control**

Current alignment: Langfuse surfaces the full OTel trace. Pi session logs are the ground truth for agent internals. Confidence and completeness signals are visible in graph state. The artifact catalogue makes every output addressable and inspectable. Nothing is hidden by design.

Future application: The gap is a unified view across all three stores — OTel traces in Langfuse, graph state in the Postgres checkpointer, artifacts in the catalogue — for a single run. Today these require separate queries. A future operational dashboard that joins these views by run ID would significantly reduce the time to diagnose unexpected behaviour. This is a tooling gap, not an architectural one, and can be built on top of the existing stores without changing any layer.

**Principle 8: Proven technology**

Current alignment: Python `contextvars` for the SDK context system — a standard library module since Python 3.7. LangGraph for orchestration, backed by LangChain's production track record. fsspec for storage abstraction, used across the scientific Python ecosystem. SQLAlchemy for the index, the most widely deployed Python ORM. Langfuse for observability, self-hostable and OTel-native. Pi-agent as the reference runtime, minimal and transparent.

Future application: The most likely pressure for unproven technology adoption is in the LLM layer. New models, new providers, new reasoning architectures will appear regularly. Pi's multi-provider abstraction (`pi-ai`) and the agent capability descriptor's per-mode model selection field provide the right mechanism — adopt new models at the agent level via configuration, not through architectural change. The system should be suspicious of any new technology adoption that requires changes to more than one layer.

**Principle 12: Genchi genbutsu**

Current alignment: Every agent session is a JSONL file. Every artifact is in the catalogue with full provenance. Every graph execution is in the Postgres checkpointer. The Langfuse trace links them. When something goes wrong, the full picture is available without asking anyone — you go and read the logs.

Future application: The genchi genbutsu principle argues against summary-only reporting as the primary operational interface. Dashboards are useful for trends, but when a specific run behaves unexpectedly, the right response is to read the pi session log, not to ask Langfuse what the average confidence was. The operational practice should reinforce this — anomalies are investigated at the source, not at the aggregate.

**Principle 13: Nemawashi**

Current alignment: The planning phase embodies nemawashi. The planner iterates with the researcher and the human until the work brief represents genuine consensus on the goal, scope, and quality criteria. Nothing expensive begins until that consensus is reached and approved. The production phase is then fast and largely deterministic because the decision-making was done upfront.

Future application: Nemawashi applies beyond the planning phase. Significant changes to the system architecture — adding a new agent, changing the artifact schema, updating a skill file that affects multiple agents — should go through a lightweight review process before being implemented. The architecture document itself is a nemawashi artifact. The principle argues for keeping it current and using it as the consensus record for architectural decisions.

**Principle 14: Hansei and kaizen**

Current alignment: The observability layer is designed to accumulate the data that enables kaizen. Langfuse actuals against declared SLA characteristics. QA revision notes surfacing recurring failure patterns. Confidence calibration data linking declared confidence to actual QA outcomes. The data infrastructure for kaizen is in place.

Future application: The data infrastructure is necessary but not sufficient. Kaizen requires a defined cadence for reviewing the data and a process for translating findings into improvements. Concretely: how often are agent system prompts reviewed against QA outcomes? Who owns skill file updates? When does a pattern of low-confidence outputs trigger a prompt revision rather than just a retry? These are process questions that need answers before kaizen becomes real rather than aspirational. The architecture enables kaizen. The operating model delivers it.

---

## Appendix A — Mario Zechner's Design Philosophy

Mario Zechner is the author of the pi-agent coding agent and the pi-mono toolkit. His design philosophy is documented in his blog post "What I learned building an opinionated and minimal coding agent" (November 2025) and in the pi-mono repository. The following is a direct summary of his stated principles.

### On minimalism

Mario's central principle is that every feature has a carrying cost — in context tokens, in maintenance burden, in cognitive load for the developer, and in the unpredictability it introduces into model behaviour. The default response to a feature request is not to add it, but to understand whether the absence of it is actually a problem. pi's system prompt is under 200 tokens. Its toolset is four tools: `read`, `write`, `edit`, `bash`. Its agent loop runs until the model says it is done, with no maximum step limit, because Mario never found a concrete use case for the limit.

This is not minimalism as aesthetic preference. It is minimalism as engineering discipline. Every thing that is not built cannot break, cannot inject unexpected context, and cannot be misunderstood.

### On observability

Mario's position is that full observability is not a feature — it is a precondition for any system that is worth trusting. His specific frustration with existing coding agents is that they inject content into the model's context without surfacing it in the UI, making it impossible to reason about why the model behaved as it did. pi surfaces everything. The session log is JSONL and is designed to be post-processed programmatically.

The split tool result pattern is a direct expression of this: what the LLM sees and what the UI shows are distinct. The LLM gets the information it needs to reason. The UI gets the structure it needs to display. Neither corrupts the other.

### On interfaces

Mario's unified LLM API (`pi-ai`) abstracts over four provider APIs — OpenAI Completions, OpenAI Responses, Anthropic Messages, and Google Generative AI — with a surface area small enough to be understood completely. His position on MCP is that it solves a distribution problem at the cost of significant context overhead, and that for most use cases the overhead is not justified. Tools are declared directly with TypeBox schemas and validated with AJV. The interface is typed, stable, and minimal.

### On composability

The pi-mono architecture is four packages: `pi-ai` (LLM API), `pi-agent-core` (agent loop), `pi-coding-agent` (CLI with session management and extensions), `pi-tui` (terminal UI). Each package knows nothing about the others except what it needs through the defined interface. You can use `pi-ai` without `pi-agent-core`. You can use `pi-agent-core` without `pi-coding-agent`. The extension system in `pi-coding-agent` is the composability mechanism for customising behaviour — lifecycle hooks at defined points, stackable, each responsible for one concern.

### On sub-agents and multi-agent systems

Mario's stated position is that spawning multiple sub-agents to implement features in parallel is an anti-pattern — it causes codebases to deteriorate unless every sub-agent's output is observable. His preferred workflow is sequential: one agent, full visibility into its output, human collaboration at each stage. His concern is not with multi-agent systems in principle but with losing the observability that makes each agent's output trustworthy.

This architecture adopts Mario's observability requirement while differing on the sequential constraint. The resolution is that the orchestrator maintains the visibility Mario insists on — every agent's output is an inspectable artifact in the catalogue, every agent's session is a persisted log — while enabling parallelism through the graph structure. The observability is not sacrificed; it is structured differently.

### On sessions

Sessions in pi are JSONL files with a documented format. They can be branched, resumed, and post-processed. The session format is a first-class design concern, not an implementation detail. This principle — that execution history is itself a structured artifact — is adopted directly in this architecture at every layer: pi session logs, LangGraph checkpointer state, artifact catalogue provenance.

---

## Appendix B — The Toyota Way: 14 Principles

Jeffrey Liker's 2004 book "The Toyota Way" codified 14 management principles underlying Toyota's production system. These are organised into four categories: Philosophy, Process, People and Partners, and Problem Solving.

### Category 1 — Philosophy

**Principle 1: Base management decisions on a long-term philosophy, even at the expense of short-term financial goals**

Toyota's decisions about investment, supplier relationships, and product development are evaluated against long-term value creation, not quarterly results. This requires accepting short-term costs in exchange for long-term capability.

### Category 2 — Process

**Principle 2: Create continuous process flow to surface problems**

Problems that accumulate invisibly in batch systems surface immediately in flow systems because each handoff is a visible event. Flow is not just about speed — it is about making problems visible as soon as they occur.

**Principle 3: Use pull systems to avoid overproduction**

Produce only what is needed, when it is needed. Pull systems are triggered by downstream demand, not upstream supply. Overproduction is waste whether the product is cars or LLM tokens.

**Principle 4: Level out the workload (heijunka)**

Uneven workloads — production spikes followed by idle periods — stress people and equipment and introduce variability. Heijunka levels demand by type and quantity over time. In practice this means designing systems that distribute work evenly rather than allowing batching to create spikes.

**Principle 5: Build a culture of stopping to fix problems, to get quality right the first time (jidoka)**

Any worker can stop the production line when a defect is detected. The line stops until the problem is fixed. This is more efficient than allowing defects to propagate downstream where they are harder and more expensive to fix.

**Principle 6: Standardised tasks and processes are the foundation for continuous improvement and employee empowerment**

Without a standard, there is nothing to improve. Standardisation is not rigidity — it is the baseline from which improvement is measured. Empowerment comes from understanding the standard well enough to identify where it falls short.

**Principle 7: Use visual control so no problems are hidden**

Information about process status should be immediately visible without requiring someone to ask for it. Andon lights, kanban boards, and production displays make the state of the system legible at a glance.

**Principle 8: Use only reliable, thoroughly tested technology that serves your people and processes**

New technology should be adopted when it solves a concrete problem that existing technology cannot. Technology for its own sake introduces unpredictability. Toyota is famously conservative about adopting automation — a human doing a task reliably is preferred over a machine doing it unreliably.

### Category 3 — People and Partners

**Principle 9: Grow leaders who thoroughly understand the work, live the philosophy, and teach it to others**

Leaders at Toyota are developed from within and are expected to understand the production system at a detailed level, not just manage it abstractly. Leadership is demonstrated through mastery of the work, not through authority.

**Principle 10: Develop exceptional people and teams who follow your company's philosophy**

The production system depends on people who understand and internalise its principles. Training and development are investments in the system's capability, not costs.

**Principle 11: Respect your extended network of partners and suppliers by challenging them and helping them improve**

Toyota treats suppliers as partners in the production system, not as commodity vendors. Challenging them to improve and helping them do so produces a more capable supply chain than price competition alone.

### Category 4 — Problem Solving

**Principle 12: Go and see for yourself to thoroughly understand the situation (genchi genbutsu)**

Managers are expected to go to the place where work happens — the shop floor, the supplier's facility, the customer's location — to understand problems directly. Decisions based on second-hand reports or aggregated data miss the detail that matters.

**Principle 13: Make decisions slowly by consensus, thoroughly considering all options, then implement rapidly (nemawashi)**

Nemawashi is the process of building consensus before a decision is made. It is slow and thorough. Once consensus is reached, implementation is fast because there is no need for renegotiation. The apparent inefficiency of slow consensus is more than recovered in the speed and quality of implementation.

**Principle 14: Become a learning organisation through relentless reflection (hansei) and continuous improvement (kaizen)**

Hansei is structured reflection on what went wrong and why. Kaizen is continuous incremental improvement. Together they create a system that learns from its own operation. Neither is a one-time event — both are ongoing practices embedded in daily work.