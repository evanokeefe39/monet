Here is the complete architecture summary.

---

## Monet extensibility architecture

### The three stable primitives

These work regardless of which graph is running. They are the guaranteed extension surface.

**`before_agent`** — fires inside the `@agent` decorator before the agent function runs. Receives `AgentRunContext` and `AgentMeta`. Returns modified `AgentRunContext` or `None`. Supports matcher syntax `agent_id(command)`. Failure raises `SemanticError`, agent never runs.

**`after_agent`** — fires inside the `@agent` decorator after the agent function returns, before the result leaves the worker. Receives `AgentResult` and `AgentMeta`. Returns modified `AgentResult` or `None`. Supports same matcher syntax. Last worker-side chance to validate, transform, or enrich.

**HITL webhook** — fires on any `interrupt()` call in any graph. Monet signs and POSTs the interrupt payload to a configured URL with a resume token. The external service handles everything — single approver, multi-approver, form collection, collaborative review, anything. When done it POSTs back to `/resume` with the consolidated decision. Monet resumes the graph. The payload is opaque in both directions — monet never inspects it. The graph author defines the interrupt shape and the expected resume shape.

---

### Graphs are the unit of extensibility

A hook event is a named point inside a graph node where the graph calls `run_hooks`. There is no separate category of hook events — there are only graph hook points. Monet ships three built-in graphs with documented hook points. Custom graphs define their own by calling `run_hooks` wherever they want them.

```python
# Inside any graph node — this is all it takes to define a hook point
async def my_approval_node(state, registry):
    obs = build_observation(state)
    obs = await run_hooks(registry, "before_approval", obs)
    decision = interrupt(obs)
    await run_hooks(registry, "after_approval", obs)
    return state
```

The graph's hook points are its documented extension API. Nothing in monet infrastructure needs updating when a new graph or new hook point is added.

**Built-in graph hook points**

```
Graph       Hook point          What it shapes
──────────────────────────────────────────────────────────────────────
Entry       after_triage        complexity, suggested agents,
                                agent allowlist, tier enforcement

Planning    before_planning     planning context, domain injection,
                                constraints, regulatory requirements

Planning    after_planning      work brief, phase limits, mandatory
                                agents, cost estimation, redaction
                                before HITL display

Execution   before_wave         wave context, wave gating, agent
                                skip based on prior results

Execution   after_wave_server   billing metering, anomaly detection,
                                OTel emission, quota enforcement

Execution   between_waves       context compression, result
                                summarisation, external data injection
```

---

### Two registries, two processes

```
Worker registry                  Graph registry
──────────────────────────────   ──────────────────────────────
before_agent, after_agent only   any event string
populated from user code         managed: monet code only
runs in worker process           self-hosted: user code too
fixed, not extensible            open, graph-defined
```

The worker registry is intentionally closed. The set of worker hook points is fixed by design — `before_agent` and `after_agent` cover everything meaningful at the agent execution boundary. Anything inside the agent is the agent's own concern. Anything that needs the graph's context belongs in the graph registry.

The graph registry is fully open. It stores handlers keyed by event string. It has no knowledge of specific event names. The graph fires an event, the registry dispatches to whatever handlers are registered for that string.

---

### `@on_hook` and registration

`@on_hook` is a declarative collector. It queues registrations at decoration time. `MonetApp.load_hooks()` imports the module, inspects the pending queue, routes worker events to the worker registry and graph events to the graph registry, and clears the queue. The user writes one file and calls `load_hooks` once — routing is automatic.

```python
# hooks/context.py — user writes this
from monet import on_hook, AgentRunContext, AgentMeta

@on_hook("before_agent", match="writer|qa")
async def inject_tone(ctx: AgentRunContext, meta: AgentMeta) -> AgentRunContext:
    tone = Path("config/tone.md").read_text()
    return {**ctx, "context": [{"role": "system", "content": tone}, *ctx["context"]]}

@on_hook("after_agent", match="*")
async def validate_output(result: AgentResult, meta: AgentMeta) -> AgentResult:
    if not result.output and not result.artifacts:
        raise SemanticError(type="empty_result", message="Agent produced nothing")
    return result

@on_hook("after_triage")   # graph hook — server side
async def enforce_allowlist(obs: TriageObservation) -> TriageObservation:
    ...
```

```python
# app.py — managed SaaS customer (worker process)
app = MonetApp()           # allow_custom_graph_handlers=False by default
app.load_hooks("myproject.hooks.context")
# before_agent → worker registry ✓
# after_agent  → worker registry ✓
# after_triage → RuntimeError at startup, clear message
```

```python
# app.py — self-hosted server process
app = MonetApp(allow_custom_graph_handlers=True)
app.load_hooks("myproject.hooks.context")
# before_agent → worker registry ✓
# after_agent  → worker registry ✓
# after_triage → graph registry  ✓
```

The flag is `allow_custom_graph_handlers`. It lives in `monet.toml`, defaults to `False`, and controls only whether user code can be placed in the graph registry. Worker hooks always pass through regardless of the flag — they run in the user's own infrastructure.

Monet's own builtin handlers bypass the flag — they are always trusted and always registered in the graph registry.

**Merge contract** — `before_agent` hooks return a full `AgentRunContext`. The decorator merges `{**original, **returned}` and restores protected fields (`run_id`, `trace_id`, `agent_id`) from the original. Hook authors spread the context and change what they care about. They cannot corrupt identity fields.

**Timeout** — every hook entry has a timeout. Default 5s for worker hooks, 10s for graph hooks. Timeout raises `SemanticError` on worker side, logged and swallowed on graph side where a metrics hook must never abort execution.

**Error handling** — worker hook failures are fatal to the agent invocation: `SemanticError` signal, agent never runs, result flows through normal routing. Graph hook failures are swallowed with logging by default — graph hooks are observational infrastructure and must not abort orchestration.

---

### Deployment modes

```
Managed SaaS
  Your server:
    MonetApp(allow_custom_graph_handlers=True)  ← your flag, your code
    load_hooks("monet.builtin.*")
    load_hooks("your.extensions.*")
    build_execution_graph(...)

  User worker:
    MonetApp()                                  ← default, flag is False
    load_hooks("their.hooks.*")
    # before_agent, after_agent only
    # graph hooks → RuntimeError at startup

Self-hosted
  User server:
    MonetApp(allow_custom_graph_handlers=True)  ← they set this explicitly
    load_hooks("their.server_hooks.*")
    build_execution_graph(                      ← override nodes or replace
        collect_wave_fn=their_collect_wave,
    )

  User worker:
    MonetApp()                                  ← still False on worker
    load_hooks("their.hooks.*")
    # before_agent, after_agent only
    # graph hooks → RuntimeError even though they own the server
    # register graph hooks on the server-side MonetApp instead

Embedded (single process)
  MonetApp(allow_custom_graph_handlers=True)
  load_hooks("their.hooks.*")
  # all hooks, one process, no enforcement needed
```

The worker never sees `allow_custom_graph_handlers`. It does not need to. The worker registry rejects graph events unconditionally — that boundary holds regardless of who owns what infrastructure.

---

### Execution graph variants

Three variants built from `build_execution_graph()` node overrides. Pick one per deployment.

**`CompliantExecutionGraph`** — multi-party approval via HITL webhook before every wave. Strict QA with zero tolerance for `LOW_CONFIDENCE` signals. Postgres checkpointer required — waves can wait indefinitely for approvals. Node overrides: `pre_wave_fn` initialises approval records and fires the interrupt, `reflect_fn` enforces stricter verdict threshold.

**`AuditableExecutionGraph`** — standard execution with hash-chained audit record at every node transition. Records identity, decisions, and artifact pointers — never content. Content lives in the catalogue. Auditors verify the chain independently. Node overrides: `pre_wave_fn`, `collect_wave_fn`, `reflect_fn` each write to the ledger before returning.

**`ZeroTrustExecutionGraph`** — no node overrides. Enforced entirely through `before_agent` and `after_agent` hooks: entitlement-based context stripping per agent, prompt injection detection, invocation logging. Standard graph topology, trust enforced at the context layer.

---

### Audit

Deferred to roadmap. The architecture supports production-grade audit by construction. The queue is the single choke point for all agent dispatch and result collection. The catalogue is the single choke point for all artifact writes. OTel spans fire at every invocation. Regulated operators intercept at these points via their own infrastructure — network proxy, queue wrapper, catalogue backend — without monet providing an audit system. When built it follows the catalogue pattern: `AuditBackend` protocol, `NoOpAuditBackend` default, implementations provided or brought by the operator.

---

### Project structure

```
my-project/
  agents/
    researcher.py        # @agent decorated, unchanged
    writer.py
    qa.py
  hooks/
    context.py           # @on_hook("before_agent"), @on_hook("after_agent")
    validation.py        # @on_hook("after_agent") output checks
  config/
    tone.md              # plain text, read by inject_tone hook
    templates/
      writer_deep.md
  monet.toml             # pools, checkpointer, allow_custom_graph_handlers
  app.py                 # MonetApp() + load_hooks() + optional graph variant
```

Tier 1 — `monet.toml` and `agents/` only, built-in graphs, no hooks. Tier 2 — adds `hooks/` with `@on_hook` decorators for `before_agent` and `after_agent`. Tier 3 — self-hosted, adds server-side graph hooks and optional graph overrides in `app.py`. Tier 4 — fully custom graphs with their own hook points, HITL webhook for external approval services. Each tier is a strict superset of the previous.