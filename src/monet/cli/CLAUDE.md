# monet.cli — CLI Entry Point

## Responsibility

Click command group. Subcommands: `dev`, `run`, `runs`, `chat`, `worker`, `server`, `status`. Each subcommand in its own module under `cli/`.

## Key rules

- No interactive wizards. Fail fast with helpful message, never offer guided setup prompts.
- `monet dev` shells to `aegra dev`. Records active example compose path in `~/.monet/state.json`. Auto-tears-down previous example containers before starting new one.
- `monet dev down` = explicit teardown.
- `monet run` invokes `default` entrypoint (or `--graph <entrypoint>` for named graphs).
- `monet runs` = list/inspect/pending/resume.
- `monet chat` = Textual TUI in `cli/chat/`.

## Discovery

`monet run <agent>:<command>` bypasses graphs — direct `invoke_agent()` call via `MonetClient`.

## What CLI does NOT own

- Graph topology or routing logic
- Queue implementation
- Config validation (delegates to `monet.config`)

## Ports

Canonical local ports in `src/monet/_ports.py`: Postgres 5432, Redis 6379, Dev server 2026, Langfuse 3000.
