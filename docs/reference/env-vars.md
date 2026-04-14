# Environment variables

Every environment variable read by monet is registered in
`monet.config._env`. This page is the single reference. When a
deployable unit starts up (`aegra serve`, `monet server`, `monet worker`,
`monet dev`) it resolves these into a typed schema and logs a redacted
summary at `INFO`, so the effective config is always observable from
the process's own logs.

Missing values fall back to the documented default. Malformed values
(for example `MONET_QUEUE_BACKEND=redi` or `MONET_AGENT_TIMEOUT=abc`)
raise `monet.config.ConfigError` at boot — never silently. This is the
Jidoka half of the configuration contract.

## Server (`aegra serve`, `monet server`)

| Variable | Type | Default | Required | Purpose |
|---|---|---|---|---|
| `MONET_API_KEY` | string | unset | yes (distributed mode) | Bearer token required by authenticated server routes. `ServerConfig.validate_for_boot` fails fast when `MONET_DISTRIBUTED=1` and the key is unset. |
| `MONET_DISTRIBUTED` | bool | `false` | no | When true, the server skips artifact-store configuration (workers own it) and enforces the `MONET_API_KEY` boot check. Accepts `1/0`, `true/false`, `yes/no`, `on/off` case-insensitively. |
| `MONET_ARTIFACTS_DIR` | path | `.artifacts` | no | Root directory for the artifact store. |
| `MONET_QUEUE_BACKEND` | enum | `memory` | no | Task queue backend. One of `memory`, `redis`, `sqlite`, `upstash`. A typo fails boot. |
| `MONET_QUEUE_DB` | path | `.monet/queue.db` | no | SQLite path when `MONET_QUEUE_BACKEND=sqlite`. |
| `REDIS_URI` | string | unset | when backend=redis | Redis connection URI. |
| `UPSTASH_REDIS_REST_URL` | string | unset | when backend=upstash | Upstash REST endpoint. |
| `UPSTASH_REDIS_REST_TOKEN` | string | unset | when backend=upstash | Upstash REST bearer token. |
| `MONET_AGENT_TIMEOUT` | float seconds | `600.0` | no | Dispatcher poll timeout for `invoke_agent`. |
| `MONET_CONFIG_PATH` | path | `Path.cwd()/monet.toml` | no | Override `monet.toml` location. Written by `monet server --config` when uvicorn's factory loader cannot receive arguments directly. |

## Worker (`monet worker`)

| Variable | Type | Default | Required | Purpose |
|---|---|---|---|---|
| `MONET_WORKER_POOL` | string | `local` | no | Pool the worker claims tasks from. |
| `MONET_WORKER_CONCURRENCY` | int | `10` | no | Max concurrent task executions. |
| `MONET_SERVER_URL` | string | unset | yes (remote) | Orchestration server URL. When set, the worker runs in remote mode. |
| `MONET_API_KEY` | string | unset | yes (remote) | Bearer token for server auth. Required whenever `MONET_SERVER_URL` is set. |
| `MONET_WORKER_AGENTS` | path | unset | no | Path to `agents.toml` for declarative agent registration. |
| `MONET_WORKER_POLL_INTERVAL` | float | `0.1` | no | Seconds between claim attempts. |
| `MONET_WORKER_SHUTDOWN_TIMEOUT` | float | `30.0` | no | Seconds to wait for graceful drain on shutdown. |
| `MONET_WORKER_HEARTBEAT_INTERVAL` | float | `30.0` | no | Heartbeat cycle in remote mode. |

The worker also validates that at least one LLM provider key
(`GEMINI_API_KEY` or `GROQ_API_KEY`) is set, so the first task that
tries to instantiate a model doesn't fail far from the cause.

## Client (`MonetClient`, `monet run`, `monet runs`, etc.)

| Variable | Type | Default | Required | Purpose |
|---|---|---|---|---|
| `MONET_SERVER_URL` | string | `http://localhost:{STANDARD_DEV_PORT}` | no | Server URL the client targets. |
| `MONET_API_KEY` | string | unset | for authenticated routes | Bearer token the client sends. |

## Graphs and entrypoints

| Variable | Type | Default | Required | Purpose |
|---|---|---|---|---|
| `MONET_GRAPH_{ROLE}` | string | role default | no | Override a graph role mapping. Example: `MONET_GRAPH_ENTRY=triage-v2`. |

## Pools

| Variable pattern | Type | Purpose |
|---|---|---|
| `MONET_POOL_{NAME}_URL` | string | Endpoint URL for pull/push pools. |
| `MONET_POOL_{NAME}_AUTH` | string | Bearer token for pull/push pools. |

`push` pools raise `ValueError` at load if neither the TOML nor the env
var supplies a URL.

## Agents

| Variable pattern | Type | Purpose |
|---|---|---|
| `MONET_{AGENT}_MODEL` | string | Override the LiteLLM model string for a reference agent. Example: `MONET_PLANNER_MODEL=openai:gpt-4o`. |

## Observability

| Variable | Source | Purpose |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTel | Explicit OTLP endpoint. Wins over all vendor shortcuts. |
| `OTEL_EXPORTER_OTLP_HEADERS` | OTel | Comma-separated `key=value` headers for the OTLP exporter. |
| `OTEL_SERVICE_NAME` | OTel | Service name in the OTel Resource. Defaults to `monet`. |
| `MONET_TRACE_FILE` | monet | When set, spans are also written as JSONL to this path (local debugging). |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | Langfuse | Derive OTLP endpoint + Basic auth header. |
| `LANGFUSE_HOST` | Langfuse | Host for the derived endpoint. Defaults to `http://localhost:{STANDARD_LANGFUSE_PORT}`. |
| `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | LangSmith | Derive OTLP endpoint + `x-api-key` header. |
| `HONEYCOMB_API_KEY` / `HONEYCOMB_DATASET` | Honeycomb | Derive OTLP endpoint + Honeycomb team/dataset header. |

`monet.config.ObservabilityConfig.otlp_endpoint_and_headers` applies
the vendor shortcuts in-memory and hands the result to the OTel
exporter as constructor kwargs. The SDK does not mutate `os.environ`.

## Reference-agent provider keys

| Variable | Agent | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | planner / writer / publisher / qa / researcher | Primary LLM provider. |
| `GROQ_API_KEY` | qa (default model) | Secondary LLM provider. |
| `EXA_API_KEY` | researcher | Exa semantic web search. |
| `TAVILY_API_KEY` | researcher | Tavily web search fallback. |
