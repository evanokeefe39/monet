# Adapter SDK Spec: Config-Based Agent Onboarding

## Problem

Every adapter in `examples/agent-adapters/` is 150-180 lines of boilerplate doing
identical work: HTTP server setup, request parsing, health endpoint, error formatting.
The agent-specific logic is 5-20 lines per adapter. This is an onboarding barrier.

A PM evaluating monet sees "write 160 lines of glue code" instead of "point monet at
your agent." Industry standard (Envoy, Kong, API Gateway) is declarative config for
the common case, thin plugin hook for the rest.

## Design

Two layers:

1. **Declarative config** (TOML) — covers 80% of agents: anything that speaks HTTP.
   Zero Python. One file per agent.
2. **Plugin hook** (single function) — covers the remaining 20%: stdio/RPC agents,
   multi-step session protocols, agents needing custom lifecycle.

Both are served by a single CLI command: `monet adapter serve <config.toml>`.

---

## Minimal Form (OpenAI-compatible agents)

Most agents in the ecosystem speak OpenAI format. For these, the entire adapter
config is three lines:

```toml
name = "my-agent"
type = "openai"
url = "http://localhost:8642"
```

Everything else is inferred:

| Field | Default | Override syntax |
|---|---|---|
| `port` | `8080` | `port = 9090` |
| `health` | `{url}/health` → `{url}/v1/models` → TCP | `health = "/custom/health"` |
| `model` | omitted from request (server decides) | `model = "deepseek-ai/deepseek-v4-pro"` |
| `auth` | `Bearer $OPENAI_API_KEY` if env set | `auth = "Bearer ${CUSTOM_KEY}"` |
| `timeout` | `300` | `timeout = 600` |
| `ready_timeout` | `120` | `ready_timeout = 180` |
| `command` | none (agent already running) | `command = ["hermes-server"]` |

**Health check cascade** (stops at first success):
1. `GET {url}/health` — explicit health endpoint
2. `GET {url}/v1/models` — OpenAI standard model list
3. TCP connect to host:port — last resort

**Auth inference**: if `OPENAI_API_KEY` is set in environment and no explicit `auth`
field is present, the adapter automatically sends `Authorization: Bearer $OPENAI_API_KEY`.
This matches the convention every OpenAI SDK uses.

**URL normalization**: if `url` has no path or ends in `/v1`, the adapter appends
`/v1/chat/completions` for requests. Health checks use the base URL.

---

## Full Config Schema (Advanced)

For agents needing custom HTTP mapping, subprocess management, or non-OpenAI protocols,
use the expanded form with sections:

```toml
# --- Minimal fields (always present) ---
name = "agent-name"
type = "http"                    # "http" | "openai" | "stdio" | "plugin"
url = "http://localhost:9000/chat"

# --- Optional top-level overrides ---
port = 8080                      # adapter listen port
timeout = 300                    # request timeout seconds
health = "/health"               # upstream health path
model = "deepseek-ai/deepseek-v4-pro"  # for openai type
auth = "Bearer ${API_KEY}"       # Authorization header value

# --- HTTP type: request/response mapping ---
[request]
body.message = "$.payload.task"
body.session_id = "$.task_id"
params = { stream = "false" }    # query params appended to url
method = "POST"                  # default POST

[response]
output = "$.message"
artifacts.report = "$.report_content"

# --- Subprocess management (any type) ---
[process]
command = ["npx", "tsx", "server.ts"]
workdir = "/pi"
ready_timeout = 120

[process.env]
PORT = "${PI_PORT:9000}"         # ${VAR:default} syntax
LLM_MODEL = "deepseek-ai/deepseek-v4-pro"

# --- Extra headers (any HTTP type) ---
[headers]
X-Custom = "value"
Authorization = "Bearer ${KEY}"  # overrides top-level auth

# --- Stdio type: plugin + RPC config ---
[stdio]
command = ["zeroclaw", "acp"]
plugin = "my_plugin:run_task"
init_rpc = "initialize"
```

---

## Upstream Types

### `type = "openai"` — OpenAI-compatible (Hermes, OpenClaw, vLLM, Ollama, LiteLLM)

Zero-config for the common case. Built-in behavior:
- Sends `{"model": model, "messages": [{"role": "user", "content": task}]}` to url
- Reads `choices[0].message.content` from response
- Handles SSE streaming (`stream: true`) transparently
- Maps `data: [DONE]` sentinel to completion
- Falls back to non-streaming if SSE connection fails

### `type = "http"` — Generic HTTP proxy

Adapter translates monet `/task` request into an HTTP call using `[request]` mapping,
extracts response using `[response]` mapping. Required fields: `url`, `[response].output`.

### `type = "stdio"` — Subprocess with JSON-RPC

For agents communicating over stdin/stdout. Requires `[stdio].plugin` pointing at a
Python function.

Plugin signature:
```python
def run_task(rpc: Callable[[str, dict], dict], message: str) -> str:
    """
    rpc: sends JSON-RPC request, blocks until result, returns result dict.
         Streaming notifications are accumulated in result["_streamed"].
    message: task string from payload.task
    returns: output text
    """
```

### `type = "plugin"` — Fully custom

Escape hatch. Entire request/response cycle handled by a Python function.

Plugin signature:
```python
def handle_task(task_id: str, payload: dict) -> dict:
    """
    Returns: {"output": "...", "artifacts": {...}}
    Raises: AdapterError("message", code="AGENT_ERROR")
    """
```

---

## What Each Agent Looks Like

### Hermes (OpenAI-compatible)

```toml
name = "hermes"
type = "openai"
url = "http://localhost:8642"
```

With subprocess launch:
```toml
name = "hermes"
type = "openai"
url = "http://localhost:8642"
command = ["hermes-server", "--port", "8642"]
```

With overrides for slow research tasks:
```toml
name = "hermes"
type = "openai"
url = "http://localhost:8642"
timeout = 600
ready_timeout = 180
command = ["hermes-server", "--port", "8642"]
```

---

### OpenClaw (OpenAI-compatible)

```toml
name = "openclaw"
type = "openai"
url = "http://localhost:3000"
```

As sidecar (agent runs in separate container, no subprocess):
```yaml
# docker-compose.yml
services:
  openclaw:
    image: openclaw/openclaw:latest
    ports: ["3000:3000"]
  adapter:
    image: monet-adapter:latest
    volumes: ["./openclaw.toml:/etc/monet/adapter.toml"]
```

---

### Nanobot (OpenAI-compatible)

```toml
name = "nanobot"
type = "openai"
url = "http://localhost:5100"
```

---

### Pi (HTTP, custom shape)

Pi has a non-OpenAI request/response format, so it needs `[request]`/`[response]`:

```toml
name = "pi"
type = "http"
url = "http://localhost:9000/chat"
health = "/health"

[request]
body.message = "$.payload.task"
body.session_id = "$.task_id"
params = { stream = "false" }

[response]
output = "$.message"

[process]
command = ["npx", "tsx", "server.ts"]
workdir = "/pi"

[process.env]
PORT = "9000"
LLM_PROVIDER = "openai"
LLM_MODEL = "deepseek-ai/deepseek-v4-pro"
OPENAI_BASE_URL = "https://integrate.api.nvidia.com/v1"
OPENAI_API_KEY = "${NVIDIA_NIM_API_KEY}"
```

---

### IronClaw (HTTP, custom REST)

```toml
name = "ironclaw"
type = "http"
url = "http://localhost:4000/api/v1/run"
health = "/api/v1/status"

[request]
body.prompt = "$.payload.task"
body.config.max_tokens = 4096
body.config.temperature = 0.2

[response]
output = "$.result.text"
artifacts.analysis = "$.result.structured_output"

[process]
command = ["ironclaw", "serve", "--port", "4000"]
```

---

### ZeroClaw (stdio JSON-RPC via ACP)

```toml
name = "zeroclaw"
type = "stdio"

[stdio]
command = ["zeroclaw", "acp", "--config-dir", "/etc/zeroclaw"]
plugin = "zeroclaw_plugin:run_task"
init_rpc = "initialize"

[process.env]
NVIDIA_API_KEY = "${NVIDIA_NIM_API_KEY}"
```

```python
# zeroclaw_plugin.py — 8 lines total
def run_task(rpc, message):
    sess = rpc("session/new", {})
    result = rpc("session/prompt", {
        "sessionId": sess["sessionId"],
        "prompt": message,
    })
    rpc("session/stop", {"sessionId": sess["sessionId"]})
    return result.get("content") or result.get("_streamed", "")
```

---

## Comparison Table

| Agent | Type | Config lines | Python lines | Before |
|---|---|---|---|---|
| Hermes | openai | 3 | 0 | n/a (planned) |
| OpenClaw | openai | 3 | 0 | n/a (planned) |
| Nanobot | openai | 3 | 0 | n/a |
| Pi | http | 16 | 0 | 162 lines |
| IronClaw | http | 14 | 0 | hypothetical |
| ZeroClaw | stdio | 8 | 8 | 183 lines |

---

## CLI Interface

```bash
# Serve adapter from config (the only command most users need)
monet adapter serve my-agent.toml

# Validate config without starting
monet adapter check my-agent.toml

# Generate starter config
monet adapter init                    # defaults to openai
monet adapter init --type http
monet adapter init --type stdio

# Quick test against a running adapter
monet adapter ping http://localhost:8080
```

---

## Implementation Scope

New package: `src/monet/adapter/`

```
src/monet/adapter/
    __init__.py          # public: serve(), AdapterError
    _config.py           # TOML parsing, default inference, validation
    _server.py           # uvicorn app: /health, /task, error formatting
    _proxy_openai.py     # type=openai: built-in request/response + SSE
    _proxy_http.py       # type=http: JSONPath mapping
    _proxy_stdio.py      # type=stdio: subprocess + JSON-RPC harness
    _proxy_plugin.py     # type=plugin: dynamic import + call
    _env.py              # ${VAR} and ${VAR:default} interpolation
    _jsonpath.py         # minimal $.field.nested extraction
    _health.py           # health cascade (endpoint → model list → TCP)
    _process.py          # subprocess lifecycle + readiness polling
```

CLI addition: `src/monet/cli/_adapter.py`

Docker base image: `monet-adapter:latest` — slim Python + the adapter package.
Dockerfile in `docker/adapter.Dockerfile`.

---

## Gateway Integration

The `pi-gateway` adapter variant is eliminated. The monet worker already injects
`MONET_GATEWAY_URL` and `MONET_TOKEN` into agent containers. Agents that want to
write artifacts use the gateway HTTP API directly. The adapter layer is not
responsible for artifact writes — that's the agent's concern.

---

## Onboarding Flow (PM View)

**OpenAI-compatible agent (80% of cases):**
1. `monet adapter init > my-agent.toml`
2. Set `url` to agent's address
3. `monet adapter serve my-agent.toml`
4. Done

**Custom HTTP agent:**
1. `monet adapter init --type http > my-agent.toml`
2. Set `url`, map request fields, set response extraction path
3. `monet adapter serve my-agent.toml`
4. Done

**Stdio/RPC agent:**
1. `monet adapter init --type stdio > my-agent.toml`
2. Write 5-10 line plugin function
3. `monet adapter serve my-agent.toml`
4. Done

Time-to-first-task: ~2 minutes for OpenAI agents, ~5 minutes for custom HTTP,
~10 minutes for stdio.

---

## Decision: Framework Change Needed?

No. The wire protocol (`/health` + `/task` + typed schemas) is correct and unchanged.
What was missing is the adapter-side runtime — a process that implements the protocol
once and lets users declare the agent-specific translation. The existing hand-written
adapters remain valid for power users who want full control.

---

## What Dies

- `examples/agent-adapters/pi/adapter.py` → replaced by `pi.toml`
- `examples/agent-adapters/pi-gateway/adapter.py` → eliminated entirely (gateway is agent's job)
- `examples/agent-adapters/zeroclaw/adapter.py` → replaced by `zeroclaw.toml` + 8-line plugin
- The onboarding instruction "copy adapter.py and modify" → replaced by "write a TOML file"
