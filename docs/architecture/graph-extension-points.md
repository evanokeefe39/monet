# Graph Extension Points

**Status:** deferred design. Not implemented.
**Trigger to implement:** first concrete user request for injection into a specific point in a built-in graph with a concrete subgraph to plug in (e.g., an "ultraplan" pre-planner, a typed review gate loop).
**Supersedes:** the `## Roadmap` → `### Open questions` → "Custom graphs integrating with default pipeline" bullet in `CLAUDE.md` (the question is now answered; the work is deferred).

## Problem

Users want to inject their own graphs into the built-in `entry`, `planning`, and `execution` graphs at **defined points**, not arbitrary locations. Two motivating cases:

1. **Ultraplan slot.** Before the built-in planner runs, user wants a subgraph that conducts an interview, performs research, branches into tangential explorations, and chooses when and what to fold back into the plan context.
2. **Review gate slot.** After each execution wave (or after execution as a whole), user wants a strict review process that may approve, request revision (loop back to planning with feedback), or abort — triggered conditionally on state.

Current extension primitives cannot satisfy these:

- **Graph hooks** (`src/monet/core/hooks.py`) are observation-only. They transform a dict, cannot add nodes, cannot reroute, cannot invoke subgraphs, cannot interrupt.
- **Custom graphs via `aegra.json` / `monet.toml [entrypoints]`** run as isolated single-graph invocations. They cannot compose with the built-in `entry → planning → execution` flow.
- **Worker hooks** (`@on_hook("before_agent" | "after_agent")`) run around a single agent invocation inside the worker. Wrong scope.
- **Signals** route to `interrupt` or `retry`, both terminal within execution. No "replan" signal group, no loop-back edge.

## Mental model: slots, not hooks

| Primitive | What it does | Where it lives | What it receives | What it may do |
|---|---|---|---|---|
| **Hook** (existing) | Transform an observation dict | Fires at fixed probe points inside a graph | `dict[str, Any]` observation | Return modified dict |
| **Worker hook** (existing) | Wrap an agent invocation | Inside worker around `before_agent` / `after_agent` | Task + context + result envelope | Mutate task, raise escalation |
| **Slot** (proposed) | Host an injected subgraph | Named, typed points in built-in graphs | Scoped state per `SlotSpec.reads` | Run subgraph (may interrupt, invoke agents, branch). Return writes + optional decision |

Slot differs from hook on three axes: it runs a **graph** (not a callable), it has a **typed state contract** (reads/writes declared, reducers declared), and it may return a **decision** the host graph routes on.

## Design

### 1. Slot catalog (SDK publishes)

Each built-in graph exports a finite, versioned set of named slots with a contract:

```python
# e.g. src/monet/orchestration/planning_graph.py
SLOTS: dict[str, SlotSpec] = {
    "pre_plan": SlotSpec(
        reads=("topic", "triage"),
        writes=("plan_context",),
        write_reducers={"plan_context": "replace"},
        decisions=None,                 # no routing — pass-through
        interruptible=True,
    ),
    "post_plan_review": SlotSpec(
        reads=("routing_skeleton", "work_brief_pointer"),
        writes=("routing_skeleton", "review_feedback"),
        write_reducers={"routing_skeleton": "replace", "review_feedback": "replace"},
        decisions=("approve", "revise", "reject"),
        interruptible=True,
    ),
}
```

`SlotSpec` is stable public API once published. Adding fields is additive; removing or narrowing `reads` / `writes` is a breaking change.

### 2. Subgraph contract (user builds)

User builds a LangGraph `StateGraph` (or equivalent compiled graph) whose input state is a `TypedDict` of the slot's `reads`, whose output merges into host state per `writes` and `write_reducers`, and whose terminal state optionally carries a `decision` field when `decisions` is non-null.

```python
# user project
class UltraplanState(TypedDict):
    topic: str
    triage: dict[str, Any]
    plan_context: str

def build_ultraplan() -> CompiledGraph:
    g = StateGraph(UltraplanState)
    g.add_node("interview", interview_node)        # may interrupt for HITL
    g.add_node("research",  research_node)         # invoke_agent allowed
    g.add_node("synthesize", synthesize_node)
    g.add_edge("interview", "research")
    g.add_conditional_edges("research", branch_fn, {...})
    g.add_edge("synthesize", END)
    return g.compile()
```

SDK provides the adapter: maps host state → subgraph state on entry, merges subgraph state → host state on exit per declared reducer.

### 3. Registration (config)

Users fill slots declaratively in `monet.toml`:

```toml
[slots.planning.pre_plan]
graph = "mymod.ultraplan:build_ultraplan"          # module:factory or entrypoint id
when  = "triage.complexity in ['high', 'ambiguous']"   # optional guard over scoped state

[slots.execution.after_wave]
graph = "mymod.gates:build_review_gate"
when  = "wave.index > 0"                            # skip first wave
on_decision.approve = "continue"
on_decision.revise  = "replan"                      # loops back to planning (adapter-level)
on_decision.reject  = "abort"

[slots.execution.after_execution]
graph = "mymod.gates:build_final_review"
on_decision.approve = "complete"
on_decision.revise  = "replan"
```

An empty slot short-circuits its conditional edge — zero cost. `when` is a restricted expression language over scoped state fields (not full Python); compiled at graph build. `on_decision.<name>` maps each declared decision to a routing intent understood by the host graph or adapter (`continue`, `complete`, `abort`, `replan`).

### 4. Candidate slots (first cut)

The set should start small. Each published slot is a stability commitment.

**Entry graph:**
- `triage_augment` — enrich or override the triage classification.

**Planning graph:**
- `pre_plan` — the **ultraplan slot**. Runs before the built-in planner. Writes `plan_context` consumed by the planner's prompt.
- `plan_augment` — post-planner, pre-approval. Validate, decorate, or rewrite `routing_skeleton`.
- `approval_gate` — replace the built-in `human_approval` interrupt with user-defined approval UX. Decides `approve | revise | reject`.

**Execution graph:**
- `before_wave` — per-wave preparation (scoped context, credential rotation).
- `after_wave` — the **review gate slot**. Decides `continue | replan | abort`.
- `after_execution` — final review. Same decision set as `after_wave`.
- `signal_handler.<group>` — custom handler for a specific `SignalType` group; replaces default routing for that group.

**Adapter (outside the graphs):**
- `post_run` — a slot owned by `monet.pipelines.default.adapter`. Cheapest place to add a review-loop-back-to-planning since the adapter already owns thread sequencing.

### 5. Replan loop wiring

The `replan` decision is the interesting routing case. It requires adapter cooperation, not graph edges alone, because re-entering planning means creating a new planning thread with the prior skeleton plus feedback. Contract:

- Slot returns `{decision: "replan", writes: {review_feedback: str, ...}}`.
- Host surfaces the writes on the final execution state.
- Adapter inspects the decision field, opens a new planning thread with input `{topic, revision_context: review_feedback, prior_skeleton: ...}`, reruns approval, reruns execution.
- Adapter enforces a **max-revisions budget** (config) to prevent infinite loops.
- Client sees a new `RunStarted` style event or a `Replanning` event (TBD — new event type in `monet.pipelines.default.events`).

### 6. Interrupts

Subgraphs in interruptible slots may call `interrupt(...)`. Bubbling these to the client requires tag namespacing:

- Built-in tags (`human_approval`, `human_interrupt`) stay as-is.
- Slot tags take the form `slot:<graph>.<slot_name>:<user_tag>`, e.g. `slot:planning.pre_plan:interview_question`.
- `DefaultInterruptTag` becomes `DefaultInterruptTag | str` at the client boundary — the pipeline's typed verbs still cover the built-ins; generic slot interrupts resolve through `client.resume(run_id, tag, payload)` directly.

### 7. Hard parts (explicit)

Each of these needs a concrete answer before implementation.

1. **State-field namespace collisions.** Two slots both declaring `writes=("plan_context",)` collide. Option A: per-slot output namespace (`state.slot_outputs["planning.pre_plan"]["plan_context"]`). Option B: static validation at registration that no two active slots share a write field. Prefer A — cleaner contract, readable state.
2. **Reducer declarations.** `routing_skeleton` must be replace-not-append; list fields may want extend. `write_reducers` on `SlotSpec` is canonical — slot author cannot override.
3. **Condition language.** `when = "triage.complexity in [...]"` needs a sandbox. Restricted AST subset (attribute access, comparison, `in`, boolean ops, literals). No function calls. Evaluated against a scoped view of host state.
4. **Agent invocation from a slot subgraph.** Slot runs in the server process (same as built-in graphs). `invoke_agent` works identically. Document that slots are server-side code.
5. **Failure policy.** Per slot: `fail_run | skip_slot | retry_slot(n)`. Default `fail_run` for strict slots (approval_gate, review gates), `skip_slot` for augmenters (triage_augment, plan_augment).
6. **Validation at boot.** `_schema.py` gains a `SlotsConfig`. At `monet dev` / `aegra serve` boot, each registered slot's `graph` factory is imported, its input schema is introspected, and mismatches against `SlotSpec.reads` fail fast with the same redacted-summary treatment other config schemas get.
7. **Decision contract enforcement.** If a slot's `SlotSpec.decisions` is non-null, the host graph must receive a valid decision. Missing or unknown decision → slot fails per failure policy.
8. **Tracing.** Slot subgraphs inherit the parent OTel span context. Slot span attributes: `monet.slot.graph`, `monet.slot.name`, `monet.slot.decision`.

## Progression (ship order)

Each phase ships standalone and is usable without the next.

1. **Adapter-level `post_run` slot only.** Simplest. Covers the review-loop-back-to-planning case directly. Validates: config schema, registration, decision routing, `replan` budget, event surface for client. No graph-internal changes.
2. **Worker-side slots in planning.** `pre_plan` (ultraplan), `plan_augment`. Validates: subgraph contract, state scoping, reducer handling, boot-time validation.
3. **Execution slots.** `before_wave`, `after_wave`, `after_execution`. Validates: slot firing per-wave, interrupt tag namespacing.
4. **Approval replacement.** `approval_gate`. Validates: interrupt tag namespacing at the HITL boundary, client-side tag parsing.
5. **Signal handler slots.** Last. Most invasive — touches `_signal_router.py` dispatch.

## Non-goals

- **Arbitrary graph rewriting.** Users fill declared points. They cannot add edges, remove nodes, or change state schemas of built-in graphs.
- **Runtime slot registration.** Slots are declared in config, validated at boot, frozen for the process lifetime.
- **Graph-level hooks replacing slots.** Existing hooks stay for pure observation. They are not a substitute.
- **SaaS-specific controls.** Tenant scoping, quota, billing — all downstream. This feature is orthogonal to Priority 1.

## Decisions required before implementation

Each of the following must be answered before picking this up. They are deliberately left open now so the first concrete user request can inform them.

- Which slots ship in phase 1 beyond `post_run`? (probably none — validate adapter model first).
- Restricted-expression language for `when`: choose a library (e.g. `simpleeval`) or implement an AST visitor in-tree.
- Should `post_run` be modeled as an adapter-plugin (module path) or as a slot with a published `SlotSpec`? Prefer the latter for uniformity, even though the adapter — not a graph — hosts it.
- New `Replanning` / `RevisionStarted` event in `monet.pipelines.default.events`, or overload `RunStarted`?
- How does the client render slot-originated interrupts? Renderer currently knows about `human_approval` / `human_interrupt` by name in `monet.pipelines.default.render`.

## Relationship to other deferred work

- **Pluggable pipeline adapters via `[entrypoints.<name>] adapter = "..."`** (see `CLAUDE.md` → `## Deferred from client-decoupling refactor`): complementary. Pluggable adapters let users *replace* the adapter; slots let users *extend* the default. Either could land first.
- **In-process driver (`_run.py`) reintroduction**: orthogonal. A reintroduced driver should consume slots the same way the adapter does.
- **Graph ↔ client interrupt wire-contract test**: prerequisite. Before shipping slots with interrupt tag namespacing, the contract test against real graphs must exist.
