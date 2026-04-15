# Chat (extended)

Registers two user-defined `@agent` capabilities and drives them from
`monet chat` via auto-discovered slash commands + from `monet run` via
direct invocation. Demonstrates the Track C surface.

## Prerequisites

- Docker Desktop
- Python 3.12+ and `uv`
- At least one LLM key (for the stock reference agents; the example
  agents are stubs and need no keys)

## Setup

```bash
cd examples/chat-extended
uv sync
cp .env.example .env
```

## Run

Terminal 1:

```bash
monet dev
```

Terminal 2 — interactive REPL:

```bash
monet chat
```

`monet chat` calls `GET /api/v1/agents` at startup and builds a
dispatch table. Type `/help` to see the agents it discovered:

```
Agents:
  /planner:fast <task>       — ...
  /planner:plan <task>       — ...
  /report_writer:draft <task> — Draft a short report from a brief.
  /search:fast <task>        — Quick keyword search (stub).
  ...
```

Invoke one:

```
> /search:fast current LLM agent frameworks
Invoking search:fast…
search:fast ok
{
  "query": "current LLM agent frameworks",
  "results": [ ... ]
}
```

The result is rendered inline AND a short summary is appended to the
conversation thread as a system message so the chat context knows the
invocation happened.

## `monet run agent:cmd` — one-shot, no REPL

Same dispatch path, non-interactive:

```bash
monet run search:fast "current LLM agent frameworks"
monet run report_writer:draft "Outline a Q3 roadmap for payments"
```

Piped stdin also works:

```bash
echo "Your task" | monet run report_writer:draft
```

Use `--output json` to emit a single JSON object of the `AgentResult`
for programmatic consumers.

## How it works

- `agents/search.py` and `agents/report_writer.py` declare their
  capabilities via the `@agent` decorator.
- `server_graphs.py` imports `agents` at module load — the decorators
  fire and register into the manifest before Aegra compiles graphs.
- The manifest endpoint (`/api/v1/agents`) reports every declared
  capability.
- `monet chat` queries the endpoint at session start; the CLI dispatch
  map contains one entry per `(agent_id, command)`.
- `monet run <agent>:<cmd>` hits the direct-invoke endpoint
  (`POST /api/v1/agents/<agent>/<cmd>/invoke`), which runs
  `invoke_agent` server-side and returns the `AgentResult` as JSON.

## Making it real

The shipped `search:fast` and `report_writer:draft` are stubs. To turn
them into working agents, replace the function bodies with real work:

```python
# agents/search.py
from tavily import AsyncTavilyClient

@search(command="fast")
async def search_fast(task: str) -> dict[str, object]:
    client = AsyncTavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return await client.search(task)
```

No changes needed elsewhere — the slash command and `monet run` paths
pick up whatever the agent returns.

## Next steps

- [chat-default](../chat-default/) — stock chat REPL.
- [custom-pipeline](../custom-pipeline/) — graph-level extension.
- [quickstart](../quickstart/) — the full default pipeline.
