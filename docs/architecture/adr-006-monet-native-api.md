# ADR-006 — monet-native API surface

Status: **proposed**
Date: 2026-04-19
Supersedes parts of ADR-005 (which punted this decision pending parity data)

## Context

The live-compat harness (`tests/compat/run.py`) drives five scenarios
through the Python (`monet.client`) and Go (`monet-tui`) clients
against a shared `monet dev` server. All five pass, but the runs
captured four concrete divergences between the wire shape Aegra
exposes today and what a clean Go client wants to depend on. These
findings are the raw material for this ADR — every decision below
traces back to one of them.

Source data: `tests/compat/_out/*.jsonl` captured 2026-04-19 against
the `chat-default` example, Aegra version per current `uv.lock`.

## Divergence findings

### F1 — Duplicate `__interrupt__` updates

Aegra emits `__interrupt__` payloads on both the subgraph's update
channel (`event: updates|planning:<id>`) and the top-level
`event: updates` channel. Both clients dutifully render the interrupt
twice in a row. Example from `approval_interrupt.go.jsonl`:

```
interrupt     step 1
interrupt     step 1    # identical payload
interrupt     step 1    # synthesized by emitTerminal from get_state
```

The third is ours (post-close poll). The first two are Aegra's
subgraph + top-level broadcast.

### F2 — `run_id` not threaded into `updates`

Aegra emits one `event: metadata` with the run_id, then every
subsequent `event: updates` carries only the node-name payload. The
Go client parses `metadata` → "run_started" and then loses the run_id
for later frames. Python's LangGraph SDK does the same — both clients
show `NodeUpdate.run_id == ""` for the rest of the stream.

### F3 — Resume tag is a node name, not a semantic verb

The scenario author must set `tag: "planning"` because that's the
name of the interrupt node in the planning subgraph. Callers can only
find this by running the graph once, peeking at `next[0]`, and
hard-coding. The interrupt payload itself has a semantic intent
(`"Approve this plan?"`) but no stable tag — changing the graph's
node name breaks every client in the field.

### F4 — No explicit `complete` SSE event

Aegra emits `event: end {"status":"success"}` or `"interrupted"`,
but our clients currently ignore `end` and synthesize a
`run_complete` / `interrupt` by polling `/threads/{id}/state` after
the stream closes. That's a round-trip per run that could be a
single event.

### Also uncovered during harness bring-up (not client-facing wire)

- Aegra rejects stream requests missing `assistant_id` with 422 but
  does not surface the error back to the client in any observable
  way — the stream just produces no updates. (Fixed by always
  setting `assistant_id` on the Go side.)
- `thread.status` commits to `"interrupted"` *after* the SSE stream
  closes. Clients that close early on `__interrupt__` race the
  commit and get a 400 on the follow-up resume. Both clients now
  drain to natural close.
- Planning subgraph terminates on `revise` without a second approval
  gate. This is a graph-design question, not a wire question, but
  clients rely on the loop behavior for the "revise-then-approve"
  UX. Either the graph should re-interrupt, or the UX should make
  the one-shot nature clear.

## Decisions

### D1 — Client-side dedupe of `__interrupt__` (short term); server dedupe (long term)

- **Now:** both clients collapse consecutive identical `interrupt`
  events with the same payload hash. Implemented in the client so
  other Aegra consumers aren't affected.
- **With monet-native API:** emit one `run.interrupted` event with
  the authoritative payload. Subgraph-vs-top-level broadcast becomes
  an internal Aegra concern, invisible to clients.

### D2 — Thread `run_id` into every stream event

- **Now:** both clients remember the first `run_id` from metadata
  and stamp it onto every subsequent `NodeUpdate`. No wire change.
- **With monet-native API:** every event object carries `run_id`,
  `thread_id`, and a monotonic `sequence_number` (see D4). Metadata
  event is still useful as a header but no longer load-bearing.

### D3 — Introduce symbolic interrupt tags

- **Wire change:** interrupt payloads gain a `tag` field that is a
  declared-by-graph identifier, not the node name. Chat's planning
  interrupt declares `tag: "plan_approval"`. Graph renames are
  invisible to clients.
- `resume` endpoint accepts either the new symbolic tag or the
  legacy node name for one release, then node-name resume is
  removed.

### D4 — `run.complete` and `run.failed` as first-class SSE events

- **Wire change:** Aegra's `end` event becomes `run.complete`
  (success) or `run.failed` (error) with explicit `final_values`
  and `error_message` respectively. Clients stop polling
  `/threads/{id}/state` after stream close.
- Sequence numbers are added at the same time (`seq` field on every
  event) so reconnects use `Last-Event-ID` to resume at the right
  index.

### D5 — Aegra removal is non-blocking on this ADR

The monet-native endpoints ship alongside Aegra's existing
`/threads/*/runs/stream` so the Python TUI and the Go TUI can
migrate independently. Aegra removal is tracked separately once
both clients talk exclusively to the new routes.

### D6 — Revise flow: graph should re-interrupt

Planning subgraph updates so that `action: revise` re-enters the
planner with the feedback and then presents the revised plan at a
second approval gate. Today's one-shot behavior is surprising for
any client that follows the UX affordance ("Revise with feedback")
and expects to see the revised plan before it runs.

## Non-decisions (deferred)

- **Switching off Aegra entirely.** Scope of this ADR is the wire
  surface, not the orchestration host. Moving off Aegra is a larger
  decision with supply-chain / reliability implications.
- **gRPC or websockets.** SSE stays. `ndjson` over POST is well
  understood by both clients and trivially cURL-able — a net win
  given this project's observability-first mandate.
- **Schema registry.** `wire_schema.json` is enough for today. If
  the compat harness ever runs against multiple server versions in
  CI, a proper schema registry becomes worth building.

## Migration plan

1. **Land client-side dedupe + `run_id` threading** (D1, D2 short
   term) — zero wire change, unblocks cleaner diffs in the compat
   harness immediately.
2. **Add `run.complete` / `run.failed` SSE events server-side** (D4).
   Clients keep state-polling as fallback until ≥ 1 point release.
3. **Add symbolic interrupt tags** (D3). Graph authors opt in; the
   `chat` planning graph is the first adopter.
4. **Re-interrupt on revise** (D6). Graph-level change, no wire
   touch. Compat harness picks up a new scenario to lock it in.
5. **Drop state-polling fallback** once telemetry shows no caller
   relies on it.

## Out-of-band follow-ups for the harness itself

- Add scenario recording for the `signal` event kind (no scenario
  currently triggers one) so signal shape is exercised in live
  parity, not just wire_schema.json.
- Add a scenario that exercises `abort` mid-run.
- Auto-capture + diff `tests/compat/openapi.json` in CI so surface
  changes are caught as PR diffs, not at runtime.

## References

- Divergence raw data: `tests/compat/_out/*.jsonl` (2026-04-19)
- Harness: `tests/compat/run.py`, `tests/compat/py_headless.py`,
  `go/cmd/monet-tui/scenario.go`
- Related ADRs: ADR-005 (Go TUI migration decision, which deferred
  this surface design)
