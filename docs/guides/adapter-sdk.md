# Adapter SDK

The adapter SDK replaces hand-written adapter boilerplate with a TOML config file and an optional plugin function. Instead of 150-180 lines of HTTP server code per agent, most agents need 3-16 lines of config.

## Quick start

### OpenAI-compatible agent (3 lines)

Most agents in the ecosystem speak OpenAI format. The entire adapter is:

```toml
name = "my-agent"
type = "openai"
url = "http://localhost:8642"
```

Serve it:

```bash
monet adapter serve my-agent.toml
```

The adapter starts on port 8080 and exposes `/health` and `/task`.

### Generate a starter config

```bash
monet adapter init                  # openai type (default)
monet adapter init --type http      # custom HTTP mapping
monet adapter init --type stdio     # subprocess + JSON-RPC
```

### Validate config without starting

```bash
monet adapter check my-agent.toml
```

### Test a running adapter

```bash
monet adapter ping http://localhost:8080
monet adapter ping http://localhost:8080 --task "write a hello world function"
```

---

## Adapter types

### `type = "openai"` — OpenAI-compatible agents

For agents that accept `POST /v1/chat/completions` (Hermes, OpenClaw, vLLM, Ollama, LiteLLM, OpenRouter).

```toml
name = "hermes"
type = "openai"
url = "http://localhost:8642"
```

**URL normalization**: if the URL has no path or ends in `/v1`, the adapter appends `/v1/chat/completions` automatically.

**Auth inference**: if `OPENAI_API_KEY` is set and no `auth` field is present, the adapter sends `Authorization: Bearer $OPENAI_API_KEY`.

**Health cascade**: `GET {url}/health` → `GET {url}/v1/models` → TCP connect.

Optional fields:

```toml
model = "deepseek-ai/deepseek-v4-pro"  # included in every request
auth = "Bearer ${API_KEY}"              # ${VAR} and ${VAR:default} syntax
timeout = 600                           # seconds (default 300)
ready_timeout = 180                     # wait for upstream before serving (default 120)
command = ["hermes-server", "--port", "8642"]  # launch subprocess on startup
```

---

### `type = "http"` — Custom HTTP agents

For agents with non-OpenAI request/response shapes.

```toml
name = "pi"
type = "http"
url = "http://localhost:9000/chat"
health = "/health"

[request]
body.message = "$.payload.task"   # JSONPath into incoming task request
body.session_id = "$.task_id"
params = { stream = "false" }     # query string

[response]
output = "$.message"              # JSONPath into upstream response
artifacts.report = "$.report"     # optional: extract named artifacts
```

**Request body mapping**: keys under `[request].body` are field names in the outgoing request body. String values starting with `$.` are JSONPath expressions extracted from the incoming monet task request (`{task_id, payload: {task, ...}}`). Non-string or non-path values are passed through as literals.

```toml
[request]
body.prompt = "$.payload.task"
body.config.max_tokens = 4096      # literal — nested via TOML dotted keys
body.config.temperature = 0.2
```

---

### `type = "stdio"` — Subprocess JSON-RPC agents

For agents communicating over stdin/stdout (e.g., ZeroClaw ACP).

```toml
name = "zeroclaw"
type = "stdio"

[stdio]
command = ["zeroclaw", "acp", "--config-dir", "/etc/zeroclaw"]
plugin = "zeroclaw_plugin:run_task"
init_rpc = "initialize"            # optional: call this RPC once on startup

[process.env]
NVIDIA_API_KEY = "${NVIDIA_NIM_API_KEY}"
```

The plugin is a single Python function:

```python
# zeroclaw_plugin.py
def run_task(rpc, message):
    sess = rpc("session/new", {})
    result = rpc("session/prompt", {
        "sessionId": sess["sessionId"],
        "prompt": message,
    })
    rpc("session/stop", {"sessionId": sess["sessionId"]})
    return result.get("content") or result.get("_streamed", "")
```

`rpc(method, params)` sends a JSON-RPC 2.0 request over stdin/stdout and blocks until the result arrives. Streaming notifications are accumulated in `result["_streamed"]`.

---

### `type = "plugin"` — Fully custom

For complete control over the request/response cycle.

```toml
name = "my-agent"
type = "plugin"
plugin = "my_module:handle_task"
```

```python
# my_module.py
def handle_task(task_id: str, payload: dict) -> dict:
    # ...
    return {"output": "result text", "artifacts": {"key": "value"}}
```

Raise `AdapterError("message", code="AGENT_ERROR")` to return a typed error response.

---

## Subprocess management

Any adapter type can launch a subprocess on startup via `[process]`:

```toml
[process]
command = ["npx", "tsx", "server.ts"]
workdir = "/pi"
ready_timeout = 120

[process.env]
PORT = "9000"
LLM_MODEL = "deepseek-ai/deepseek-v4-pro"
OPENAI_API_KEY = "${NVIDIA_NIM_API_KEY}"
```

The adapter waits for the upstream to become healthy before serving requests. On shutdown, it sends SIGTERM with a 5-second grace period before SIGKILL.

Top-level `command` is shorthand for `[process].command`:

```toml
name = "hermes"
type = "openai"
url = "http://localhost:8642"
command = ["hermes-server", "--port", "8642"]  # same as [process].command
```

---

## Environment variable interpolation

All string values support `${VAR}` and `${VAR:default}` substitution:

```toml
url = "http://localhost:${PI_PORT:9000}"
auth = "Bearer ${API_KEY}"

[process.env]
OPENAI_API_KEY = "${NVIDIA_NIM_API_KEY}"
```

Missing variables without a default raise an error at startup.

---

## Docker

Use the `monet-adapter` base image to run any TOML-configured adapter:

```dockerfile
FROM monet-adapter:latest
COPY my-agent.toml /etc/monet/adapter.toml
```

Or as a sidecar in Docker Compose:

```yaml
services:
  my-agent:
    image: my-agent:latest
    ports: ["8642:8642"]

  adapter:
    image: monet-adapter:latest
    volumes:
      - ./my-agent.toml:/etc/monet/adapter.toml
    ports: ["8080:8080"]
    depends_on: [my-agent]
```

Build the image from source:

```bash
docker build -f docker/adapter.Dockerfile -t monet-adapter:latest .
```

---

## Error codes

The adapter returns typed error responses on failure:

| HTTP | `error_code` | Cause |
|---|---|---|
| 400 | `INVALID_REQUEST` | Malformed request body |
| 500 | `AGENT_ERROR` | Plugin raised an exception |
| 502 | `UPSTREAM_ERROR` | Upstream agent returned an error |
| 503 | `NOT_READY` | Adapter not yet initialized |
| 504 | `TIMEOUT` | Request exceeded timeout |
