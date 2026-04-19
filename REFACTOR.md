# Monet Go Chat TUI Migration — Specification

## Purpose

Replace the Python Textual-based chat TUI (`src/monet/cli/chat/`) with a Go + Bubble Tea implementation distributed as a single static binary. This document is the intent + decisions; the implementation plan follows the confirmation pass.

## Scope

**In scope.** A new Go chat TUI that connects to the existing monet server, streams chat turns, renders HITL interrupts, shows progress events, manages threads, and supports slash commands. A Go client library that the TUI is built on, covering the subset of server operations the chat TUI needs.

**Out of scope for phase one.** Replacing the Python client (`MonetClient`). Monet-native HTTP API redesign (that's option three, deferred). Server-side changes beyond bug fixes called out below. Other CLI commands (`monet dev`, `monet run`, `monet runs`) — those stay Python.

## Strategy

**Option one + pinning, then option three later.** Use the third-party Go LangGraph SDK (`github.com/KhanhD1nh/langgraph-sdk-go`) pinned to a specific commit hash for the Aegra-provided endpoints (threads, runs, state, SSE streaming). Hand-write a small Go HTTP client for monet's custom routes (capabilities, workers, tasks, thread metadata). Pin Aegra in Python dependencies to the same version the Go SDK targets.

Ship the Go TUI. Use it heavily for two to three months. Then write the option three specification informed by real usage friction, and migrate the server to a monet-native API. Do not attempt option three before the Go TUI is in use — specs written without usage data end up specifying the wrong things.

## Architecture

**What the Go client talks to.** Two server surfaces, both reached via HTTP/SSE.

The Aegra surface provides thread CRUD, thread metadata search, run streaming (SSE), and state inspection. The Go LangGraph SDK wraps these. All thread/run operations go through the SDK.

The monet surface provides capability listing, worker/task management, artifact queries, and chat-specific convenience (thread rename, history extraction). These are hand-written Go against monet's FastAPI routes mounted under Aegra.

**Authentication.** Single `MONET_API_KEY` sent as `Authorization: Bearer <key>` on every request to both surfaces. When unset, server is in keyless dev mode and accepts unauthenticated requests — the client should send the key if available and not fail if the server doesn't require it.

**Event stream semantics.** The client subscribes to one SSE stream per chat turn via the Aegra runs endpoint with `stream_mode=["updates","custom"]` and `stream_subgraphs=True`. Three event categories matter to the TUI:

Assistant message deltas arrive as `updates` events whose payload contains a `messages` list with role `assistant`. The client extracts content strings and renders them as streaming assistant output.

Progress events arrive as `custom` events whose payload has an `agent` field. The client constructs a typed `AgentProgress` and renders as advisory transcript lines. Progress is best-effort, lossy, and causally unordered relative to node updates. The client must tolerate late-arriving progress events for nodes whose results have already rendered.

Interrupt events arrive when `state.next` is non-empty after a stream completes. The client fetches the pending interrupt payload, renders the form schema, and collects the user's resume payload. The resume is sent as a new stream with `command={"resume": payload}`.

## Client operations

The Go client surface is a deliberate subset. Each operation maps to either an SDK call or a hand-written HTTP request.

**Chat thread management.** Create chat thread (SDK `threads.create` with `metadata={monet_graph: chat_graph_id, monet_chat_name: name}`). List chat threads (SDK `threads.search` with metadata filter on `monet_graph`, then hand-written HTTP call for message count via `/api/v1/chats/{thread_id}/message_count` or state inspection). Get history (SDK `threads.get_state`, extract `values.messages`). Rename thread (SDK `threads.update` with metadata patch). Delete thread (SDK `threads.delete`).

**Turn execution.** Send message (SDK `runs.stream` with `input={"messages": [{"role":"user","content":msg}]}`). Resume interrupt (SDK `runs.stream` with `command={"resume": payload}`). Get pending interrupt (SDK `threads.get_state`, inspect `next` and `tasks[*].interrupts[*].value`). Recover after disconnect (get state, if `next` non-empty render interrupt form).

**Capability discovery.** List capabilities (hand-written GET `/api/v1/capabilities` returning `[{agent_id, command, pool, description, worker_ids}]`). List slash commands (hand-written GET `/api/v1/slash_commands` returning the ordered list including `/plan` plus `/agent:command` entries).

**Artifact operations.** List recent artifacts for a thread (hand-written GET `/api/v1/artifacts?thread_id=<id>&limit=50`). Copy artifact view URL (client-side construction from server base + `/api/v1/artifacts/{id}/view`).

**Run introspection.** List recent runs for `/runs` slash command (hand-written GET `/api/v1/runs?limit=20`).

## Event typing

The Go client exposes events as a sum type over a sealed interface. The TUI consumes via a channel.

```
type Event interface { isEvent() }

type AssistantDelta struct {
    RunID   string
    Content string
}

type AgentProgress struct {
    RunID    string
    AgentID  string
    Command  string
    Status   string
    Reasons  string
}

type InterruptRequested struct {
    RunID    string
    Tag      string
    Form     FormSchema  // prompt + fields + context
}

type NodeUpdate struct {
    RunID  string
    Node   string
    Update map[string]any
}

type RunComplete struct {
    RunID       string
    FinalValues map[string]any
}

type RunFailed struct {
    RunID string
    Error string
}
```

Progress events carry `run_id`, `agent_id`, `command` as required fields. Server-side work is required to attach `run_id` to the wire payload (currently dropped — see bug list below).

## HITL form schema

Preserved verbatim from the current protocol. Forms carry `prompt` (string), `fields` (list), and optional `context` (dict). Fields have `name`, `type`, and type-specific keys.

Field types the Go TUI must render: `text`, `textarea`, `radio`, `checkbox`, `select`, `int`, `bool`, `hidden`. The `select_or_text` type is accepted but may fall back to `text` input in phase one.

Rendering path: approval-form shape (single radio with approve/revise/reject options plus optional feedback textarea) gets a compact inline picker. All other shapes get a generic vertical form. The current Python protocol matching logic in `_protocols.py` is the reference — port the structural matching, not the field-name matching.

Resume payloads are dicts keyed by field name. Hidden fields carry through their default values. Approval replies use `{"action": "approve|revise|reject", "feedback": str}`.

## TUI scope — phase one

The minimum viable chat TUI. Build this first, ship it, use it.

Streaming transcript with role-tagged lines (`[user]`, `[assistant]`, `[progress]`, `[info]`, `[error]`). Bubbles `viewport` component for scrollback. Prompt `textarea` at the bottom for input.

Slash command completion with ghost-text suggestion plus a dropdown showing the top matches with descriptions. Tab accepts the suggestion. Enter submits.

HITL approval form inline — only the approval shape (approve/revise/reject). Other form shapes render as transcript text with typed-reply fallback (same as Python's current fallback path).

Thread management: create on first submission, rename via command, delete via command. `/new`, `/clear`, `/switch <id>`, `/threads` (list with picker).

Connection handling: one SSE stream per turn, graceful reconnect on transient errors, crash-recovery via get-state on mount when the thread has a pending interrupt.

Startup: connect, show welcome splash if no history, load server slash commands.

Keybindings: `ctrl+c` twice to quit, `ctrl+q` immediate quit, `esc` close popups, `tab` accept suggestion, `enter` submit, arrow keys in dropdowns.

Log to file for post-mortem debugging (stdout is consumed by the TUI).

## TUI scope — phase two

Added after phase one is in regular use.

Sidebar picker (threads, agents, artifacts) with breakpoint-based fallback to fullscreen picker on narrow terminals. Generic HITL form widget for non-approval shapes. Markdown rendering of assistant messages via Glamour. Animated welcome screen (ASCII fire or similar). Run listing (`/runs` command). Color palette customization (`/colors` command). Theme switching. Command library / keyboard shortcuts / about screens.

## TUI scope — phase three

Delete the Python TUI. Switch to monet-native HTTP API once option three spec is written. Invest in polish — spring-based animations, progress bars for long-running planner calls, improved diff-rendering for streaming updates.

## Distribution

Single static Go binary built via `go build`. Released as versioned artifacts on GitHub Releases for Linux (amd64, arm64), macOS (amd64, arm64), and Windows (amd64). Installation is `curl | sh` or download-and-run.

The Go TUI ships independently of the Python monet package. A user can install Python monet without the Go TUI (they get the Python TUI). A user can install the Go TUI without Python monet if they're connecting to a remote server.

Version compatibility: the Go binary declares a compatible server version range (e.g. `monet-server >=0.1.0, <0.2.0`). On startup, after health-check succeeds, the client reads the server version from `/api/v1/health` and warns if outside the range.

## Pinning and upgrade policy

`github.com/KhanhD1nh/langgraph-sdk-go` pinned to a commit hash in `go.mod`. Vendored via `go mod vendor` so builds survive upstream disappearance.

Aegra pinned to a specific version in `pyproject.toml` matching the version the Go SDK targets.

LangGraph pinned in monet's Python dependencies to the version Aegra expects.

Compatibility test script runs Python client and Go client against the same dev server, compares outputs for core flows (send message, receive interrupt, resume, list threads, rename). Runs in CI on every PR. Detects drift early.

Deliberate upgrade cycle every three to six months: bump Aegra and SDK together, run compatibility tests, fix what breaks. If upgrades start breaking things more than once per cycle, that's the signal to accelerate option three.

## Pre-existing issues to fix regardless of migration

These bugs and design gaps exist in the current Python code. They are worth fixing whether or not the Go migration happens — the migration work should not be the excuse to defer them. Fixing them before the Go client lands also means the Go client inherits correct behavior rather than porting bugs into a second language.

**Progress events drop `run_id` on the wire.** `_build_agent_progress` in `src/monet/client/chat.py` hardcodes the run_id parameter to an empty string because the server-side payload does not include it. The server has `run_id` available at every emission site — orchestration code at `execution_graph.py:246` calls `emit_progress` with full context, and the worker knows it from the TaskRecord. The fix is on the server re-injection path: include `run_id` in the payload when re-emitting queue-delivered progress events into the LangGraph stream writer. Low risk, high value — without this, progress lines cannot be correctly attributed when multiple turns are in flight.

**Silent task drop on restart when `MONET_API_KEY` is unset.** `src/monet/server/_aegra_routes.py:64-67` skips recovery of in-flight push dispatches with a log line rather than failing loudly. If `MONET_API_KEY` is unset at restart for any reason (config typo, deployment miss), every recovering task silently disappears. The fix: hard-fail at lifespan startup rather than log-and-skip, consistent with how `_auth.py:65` handles the runtime case.

**`_await_completion` race on repeated awaits.** `src/monet/queue/backends/memory.py` pops the task and completion event from its stores when `_await_completion` returns. If two code paths both await the same `task_id` (possible under retry or bug), the second gets `KeyError`. Not currently triggered in production but a latent issue — the method should be idempotent or the protocol should forbid repeated awaits explicitly.

**Textual TUI fragility touching framework internals.** `ChatApp._handle_exception` reaches into `self._exception` and `self._exception_event` directly. These are Textual internals and have moved between versions. Replace with the public `self.exit(return_code=1)` API plus the existing `_crash_error` stash. Relevant only while the Python TUI remains in use; not a concern post-migration.

**Pulse toggle in options screen is not wired.** `_themes.py` / `_menu.py` / `_app.py` expose a pulse on/off toggle in the options modal, but the actual `PULSE_ENABLED` constant is read once at module import from the env var. Either wire the runtime toggle through to `_set_busy` / the `BorderPulseController`, or remove the toggle option from the menu. The current state is visible UX debt.

**Five-second indicator poller runs unconditionally.** `_refresh_indicator_async` in `_app.py` polls `list_capabilities` and `artifacts.query_recent` every five seconds regardless of window focus or activity. At 24 tmux panes this is 24 simultaneous pollers hitting the same server. Mitigation: back off to 30s when idle, or make it event-driven on turn completion and capability-heartbeat signals. Relevant for the Python TUI while it remains; the Go TUI should be designed with this already in mind.

**OSC 52 clipboard silently fails in tmux.** `copy_to_clipboard` in the Python TUI uses OSC 52, which tmux blocks by default unless `set -g set-clipboard on`. Users get no feedback when it fails. Fix: detect tmux and fall back to writing the transcript to `/tmp/monet-transcript-<pid>.txt` with a notify showing the path. Same fallback should be used in the Go TUI from day one.

## Server-side work required for the Go client

Minimum changes the Go client depends on — in addition to the pre-existing issues above. Each is a small, contained fix.

Expose `/api/v1/health` returning `{version: str, queue_backend: str, uptime_seconds: float}`. Used by the Go client for version compatibility check and startup smoke test.

Expose `/api/v1/capabilities` if not already present. Python client uses `client.list_capabilities()` which presumably hits some route — confirm the exact endpoint during implementation.

Expose `/api/v1/slash_commands` returning the ordered slash list. Same as above — confirm the endpoint.

Expose `/api/v1/chats/{thread_id}/message_count` so the Go client can populate the thread list without fetching full history per thread. Current Python client reads state values and counts messages, which is expensive across many threads.

## Testing strategy

Unit tests for the Go client cover event parsing, form schema handling, error classification, and HTTP error mapping. Table-driven tests using recorded SSE traces from a real server as fixtures.

Integration tests run the Go client against a real monet dev server in CI. Cover the core chat flows: simple turn, turn with progress events, turn with approval interrupt, turn with revise-and-retry, thread switching, thread deletion, server restart mid-stream, connection loss and reconnect.

Compatibility tests (mentioned above) run Python and Go clients side-by-side against the same server and compare event streams plus final state for identical inputs.

TUI tests use Bubble Tea's `teatest` package for golden-file testing of TUI state transitions.

## Naming and layout

Go module path: `github.com/evanokeefe39/monet-cli` (separate repo from Python monet, since binary distribution cadence differs).

Layout:
```
cmd/monet/           # main.go — CLI entrypoint
internal/client/     # Go monet client (SDK wrapper + custom routes)
internal/tui/        # Bubble Tea app
internal/events/     # Event types + SSE parsing
internal/hitl/       # HITL form rendering
internal/config/     # Config file + env var handling
```

The Go binary is named `monet`. Installation replaces or coexists with the Python `monet` command via `PATH` precedence. Users who want both keep the Python one as `monet-py`.

## Known issues preserved from the Python TUI

These behaviors are preserved in the Go TUI because they work well:

Two-press `ctrl+c` to quit with a five-second confirm window.

Lazy thread creation — first user submission creates the thread, not the TUI startup. Empty sessions don't spam the thread list.

HITL interrupts render in-flow in the transcript (or via widgets for recognized shapes), and the next user submission is parsed as the resume. No modal dialogs.

Width-based sidebar vs fullscreen picker dispatch (phase two).

Transcript role-tag colors with `/colors` customization (phase two).

Clipboard fallback: when OSC 52 fails (tmux without `set-clipboard on`), write the transcript to `/tmp/monet-transcript-<pid>.txt` and show a notification with the path.

## Decisions confirmed

These are settled — do not re-litigate during implementation.

Option one with pinning for phase one/two. Option three later.

One SSE stream per turn — client does not subscribe to the queue directly. Queue is server-internal.

Progress events are best-effort, lossy, unordered relative to node updates. Client must tolerate this.

Alongside, not replace — the Go client talks to both Aegra endpoints and monet endpoints. The server partitioning is deliberate, not a temporary shim.

Bubble Tea + Lip Gloss + Bubbles for the TUI. Glamour added in phase two for markdown. Harmonica optional for phase three animations.

Single static binary distribution, independent versioning from Python monet.

Compatibility test suite as the CI-visible contract between the two clients.

## Decisions deferred

These are open questions for the implementation plan pass.

Sequence numbers on events for at-least-once resumption. Would require server-side change to emit sequence-numbered events and client-side tracking of last-seen sequence. Easy to spec upfront, painful to retrofit, but not required for phase one correctness. Decide before phase two.

Typed progress event variants vs opaque status string. Current payload is `{status: str, reasons: str, ...}` plus arbitrary extra keys. Decision: keep as-is for phase one (opaque), revisit when the planner's progress reporting stabilizes.

Whether the Go TUI should have its own welcome screen / brand identity distinct from the Python one, or stay visually identical for continuity. Recommendation: distinct, so users know they're on the new binary, but preserve the same core layout.

## Expected friction during implementation

Flagging the items most likely to cause rework so the implementation plan addresses them up front.

The third-party Go SDK may have gaps in SSE reconnection handling, subgraph event propagation, or error response parsing. Budget time to find and work around these. Contributing fixes upstream is the preferred path; forking is the fallback.

The Python client's event filtering in `_stream_chat_with_input` (filtering updates patches for dicts with a `messages` key, pulling assistant role entries) is specific to LangGraph's schema and must be ported faithfully. Test against real traces, not guesses.

The `_extract_interrupt_payload` logic walking LangGraph's task/interrupts/value nesting is fragile. Reproduce exactly in Go — this is the single highest-risk port since interrupt handling is the most user-visible error if it breaks.

HITL form rendering is a lot of widget code. Phase one does approval only; generic forms land in phase two. Don't let scope creep pull generic forms into phase one.

Progress event attribution (the `run_id` server-side fix) must land before the Go client launches, otherwise `[progress]` lines have no run context and cross-turn noise is possible.

## What Claude Code should verify before implementing

Run through this checklist with the codebase open, confirm or flag each item:

1. Enumerate every HTTP endpoint the current Python client hits. Compare against the operations list above. Flag anything missing.

2. Confirm that `stream_subgraphs=True` is required for progress events from execution subgraphs to reach clients. Check by running a local planner scenario with and without the flag.

3. Identify the exact server-side code that bridges queue-delivered progress events into the LangGraph stream writer. Confirm it has `run_id` in scope and can include it in the emitted payload.

4. Read `orchestration/_invoke.py` end-to-end. Document the task dispatch path, completion await, and progress subscription in the implementation plan.

5. Read `server/_routes.py` end-to-end. List every monet-owned route, its auth requirement, request shape, response shape. This is the definitive list of routes the Go client's custom HTTP code covers.

6. Check the third-party Go SDK (`KhanhD1nh/langgraph-sdk-go`) actually supports `stream_subgraphs=True` and custom stream mode. Verify the field names in the streamed chunks match what Python `_wire.py` expects.

7. Validate that pinning Aegra works in practice — install the pinned version in a clean venv, run `monet dev`, confirm nothing breaks.

8. Propose the exact go module path, CI setup, release tooling, and version compatibility check wire format. None of these are specified above — they're implementation details for the plan.

9. Flag any item in "Server-side work required" that would actually be invasive. If `/api/v1/chats/{thread_id}/message_count` requires schema changes, call it out. If it's a five-line function, confirm and proceed.

10. Produce the phase one implementation plan with concrete steps, file-level structure, test setup, and an estimated ordering of work. Call out any decisions above that the implementation contradicts or reveals to be wrong.