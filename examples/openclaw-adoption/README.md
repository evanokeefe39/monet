# openclaw-adoption

Four agents — pi, zeroclaw, hermes, openclaw — registered as monet commands and
composed by the planner. No Python source required; all agents are external
adapters declared in `agents.toml`.

The planner reads each agent's description and routes subtasks accordingly. A
single `monet run` goal can fan out across all four agents in one DAG execution.

## Agents

| Agent | Command | Protocol | Description |
|---|---|---|---|
| `zeroclaw` | `run` | zeroclaw (subprocess ACP) | Shell-native execution: runs scripts, installs packages, inspects outputs |
| `pi` | `code` | http (JSONPath) | Code generation: writes functions, modules, test suites |
| `hermes` | `reason` | openai | Reasoning and writing: analysis, documentation, Q&A |
| `openclaw` | `code` | openai | Agentic code editing: multi-file changes, debugging, verification |

## Prerequisites

- monet installed (`uv add monet` or `pip install monet`)
- Docker Desktop running (postgres + redis via `monet dev`)
- `NVIDIA_NIM_API_KEY` in `.env` (zeroclaw uses NIM by default)
- `zeroclaw` CLI on `PATH` with config at `~/.zeroclaw` (or set `ZEROCLAW_CONFIG_DIR`)
- Pi server running on port 9000 (or update `agents.toml` `transport.url`)
- Hermes server running on port 8642 (or update `agents.toml` `transport.url`)
- OpenClaw server running on port 3000 (or update `agents.toml` `transport.url`)

## Setup

```bash
cp .env.example .env
# edit .env and fill in NVIDIA_NIM_API_KEY (and others as needed)
```

## Startup sequence

Start the agents themselves (Pi, Hermes, OpenClaw) per their own docs. Zeroclaw
requires only the `zeroclaw` CLI on `PATH` — monet spawns it as a subprocess per task.

Then, from this directory:

```bash
# Start monet server + postgres + redis
monet dev

# In a new terminal — start the worker and load all four agents
monet worker --server-url http://localhost:2026 --agents agents.toml
```

## Invoking the planner

```bash
# Single-agent task — planner routes to pi
monet run "implement a binary search function in Python with docstring and type annotations"

# Two-agent task — planner routes pi then zeroclaw
monet run "write a fibonacci function and run it to verify the output for the first 10 terms"

# Analysis task — planner routes to hermes
monet run "explain the tradeoffs between merge sort and quicksort and when to use each"

# Complex multi-agent task — planner fans out across agents
monet run "implement a CLI argument parser, write unit tests for it, run the tests, and summarize the results"
```

## Interactive TUI

```bash
monet chat
```

Type a goal at the prompt. The planner proposes a plan for your approval before
execution. Use `/plan` to iterate on the plan without committing to execution.

## Configuration

**Change agent URLs.** Update `agents.toml` transport URLs and restart the worker.

**Change zeroclaw config dir.** Set `ZEROCLAW_CONFIG_DIR` env var, or add
`transport.config_dir = "/path/to/config"` to the zeroclaw entry in `agents.toml`.

**Change models.** For openai-protocol agents (hermes, openclaw), the model is
determined by the server they point at. For zeroclaw, edit the zeroclaw config
at `~/.zeroclaw/config.toml`. See `docs/reference/models.md` for the full
model catalog and provider fallback chain.

**Add an agent.** Append a `[[agent]]` entry to `agents.toml` and restart the
worker. The planner picks it up immediately from the updated manifest.
