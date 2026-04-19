# ADR 005 — Go TUI migration (monet-tui)

Status: Accepted (branch `refactor`, 2026-04-19)

## Context

`monet chat` was a Textual (Python) TUI embedded in the `monet` package at
`src/monet/cli/chat/`. It required the full `monet` runtime to be installed
(LangGraph, FastAPI, OpenTelemetry, all agent dependencies) even on machines
that only run the CLI. The Python runtime is also not distributable as a
self-contained binary, which makes installation in restricted environments
awkward and first-run latency visible.

Specific pain points:

1. Installing `monet` in a Python environment that already has conflicting
   versions of LangGraph or Pydantic required dependency negotiation even for
   users who only wanted `monet chat`.
2. The Textual TUI's rendering model had no well-defined wire contract; the
   chat CLI was coupled to Python-internal event types, making it hard to
   specify what a non-Python client should implement.
3. Textual lacked a cross-platform static binary distribution path.

## Decision

Rewrite the `monet chat` TUI as a standalone Go binary (`monet-tui`) that
speaks the monet HTTP+SSE wire protocol only. The Go module lives at
`go/` inside the monorepo, under the module path
`github.com/evanokeefe39/monet-tui`. It is released separately via GoReleaser
as pre-built binaries for linux/amd64, linux/arm64, darwin/amd64, darwin/arm64,
and windows/amd64.

`monet-tui` is a standalone binary users invoke directly. `monet chat` (the
Python Textual TUI) is unchanged. The two binaries coexist until `monet-tui`
reaches full feature parity, at which point the Python TUI is deleted — no
dispatch shim, no env-var toggle. This keeps the boundary clean ahead of an
eventual `git subtree split go/` into a separate repository.

Wire types shared between Python server and Go client are governed by
`tests/compat/wire_schema.json`. Both the Python compat test
(`tests/compat/test_wire_compat.py`) and the Go contract test
(`go/tests/contract/wire_compat_test.go`) must pass before any change to wire
types is merged.

## Wire protocol

The Go client talks to the Python server via:

- `GET /api/v1/health` — binary compatibility check on startup
- `POST /api/v1/threads` — create or resume a chat thread
- `GET /api/v1/threads` — list threads
- `POST /api/v1/runs/stream` — start a run, SSE stream of events
- `POST /api/v1/runs/{run_id}/resume` — resume a paused interrupt
- `DELETE /api/v1/runs/{run_id}` — abort a run
- `GET /api/v1/agents` — list capabilities (slash-command discovery)
- `GET /api/v1/artifacts` — list artifacts for a thread

SSE event types and their required JSON keys are in `COMPATIBILITY.md`.

## Consequences

Positive:
- `monet-tui` installs as a single static binary with no runtime dependencies.
- Wire protocol is now a first-class contract with automated compatibility
  checks.
- Go's goroutine model simplifies concurrent SSE reading and input handling
  without the async complexity of Textual's event loop.

Negative:
- Two languages in one repo. Go CI is separate from Python CI.
- Breaking wire changes now require coordinated bumps in both Python server and
  Go client.
- Feature parity window: Go TUI does not yet support all Textual TUI features
  (wave preview renderer, deeplinks). Tracked in ISSUES.md.

## Alternatives considered

- **WebSocket instead of SSE** — Aegra's LangGraph Platform compatibility layer
  exposes SSE; switching to WebSocket would require Aegra changes with no
  functional benefit.
- **Rust instead of Go** — Bubble Tea (Go) ecosystem directly solves the TUI
  problem; Ratatui (Rust) is comparable but team has no Rust experience.
- **Keep Python, ship as PyInstaller binary** — PyInstaller binaries are large
  (~80 MB) and fragile across OS versions; Go produces clean 10 MB static
  binaries.
