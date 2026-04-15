# Chat (default)

The stock `monet chat` REPL with no customisation. Demonstrates multi-turn
conversations persisted across CLI restarts via Aegra threads + Postgres
checkpointing.

## Prerequisites

- Docker Desktop (auto-provisions Postgres via `monet dev`)
- Python 3.12+ and `uv`
- At least one LLM key (`GEMINI_API_KEY` or `GROQ_API_KEY`)

## Setup

```bash
cd examples/chat-default
uv sync
cp .env.example .env
# Fill in at least one LLM provider key
```

## Run

Terminal 1 — start the server:

```bash
monet dev
```

Terminal 2 — open the REPL:

```bash
monet chat
```

Type a message, press Enter. The server calls the chat graph, streams
the response back, and checkpoints the conversation. Quit with `/quit`
or Ctrl+C. Relaunching `monet chat` picks up where you left off.

## Features

### Session management

- `monet chat` — resume the most recent conversation.
- `monet chat --new` — start a fresh one.
- `monet chat --list` — show all sessions with names, message counts,
  and last-active timestamps.
- `monet chat --session work-notes` — open (or create) a named session.
- `monet chat --resume <thread_id>` — resume a specific session by id.

### In-REPL commands

- `/help` — list available slash commands and any agent capabilities.
- `/name <name>` — rename the current session.
- `/history` — print the full conversation transcript.
- `/runs` — list recent multi-step pipeline runs.
- `/graphs` — list graphs registered on the server.
- `/quit`, `/exit` — leave the REPL.

### Dynamic agent discovery

If any `@agent` capabilities are registered on the server, `monet chat`
discovers them at session start and exposes a `/<agent_id>:<command>`
slash command for each one. This example ships with the stock
reference agents (planner, researcher, writer, qa, publisher) — type
`/help` after connecting to see them.

Example:

```
> /planner:fast What's the current time?
planner:fast ok
{"complexity": "simple", "suggested_agents": [], ...}
```

## Next steps

- [chat-extended](../chat-extended/) — register your own `@agent`s and
  invoke them from the REPL.
- [custom-pipeline](../custom-pipeline/) — compose the compound graph
  with your own review node via `MyRunState(RunState)`.
- [quickstart](../quickstart/) — run the full default pipeline
  (entry → planning → execution) with `monet run`.
