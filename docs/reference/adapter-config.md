# Adapter Config Reference

Full schema for `monet adapter serve <config.toml>`.

## Top-level fields

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Adapter name (used in logs) |
| `type` | `"openai" \| "http" \| "stdio" \| "plugin"` | required | Upstream protocol |
| `url` | `str` | required for openai/http | Upstream base URL |
| `port` | `int` | `8080` | Adapter listen port |
| `timeout` | `int` | `300` | Request timeout in seconds |
| `health` | `str \| null` | `null` | Health check path for http type (e.g. `"/health"`) |
| `model` | `str \| null` | `null` | Model name included in openai requests |
| `auth` | `str \| null` | `null` | Authorization header value (supports `${VAR}`) |
| `ready_timeout` | `int` | `120` | Seconds to wait for upstream before serving |
| `command` | `list[str]` | `[]` | Shorthand for `[process].command` |
| `plugin` | `str \| null` | `null` | Plugin import path for type=plugin (`"mod:fn"`) |

## `[request]`

Controls how the monet `/task` request is translated into the upstream HTTP request. Used only with `type = "http"`.

| Field | Type | Default | Description |
|---|---|---|---|
| `body` | `dict` | `{}` | Body template. String values starting with `$.` are JSONPath expressions extracted from the incoming request. |
| `params` | `dict[str, str]` | `{}` | Query parameters appended to the URL |
| `method` | `str` | `"POST"` | HTTP method |

The incoming request structure available for JSONPath extraction:

```
{
  "task_id": "<string>",
  "payload": {
    "task": "<string>",
    ...
  }
}
```

Example:

```toml
[request]
body.message = "$.payload.task"   # -> outgoing body: {"message": "<task>"}
body.session = "$.task_id"        # -> outgoing body: {"session": "<task_id>"}
body.config.stream = false        # literal value -> {"config": {"stream": false}}
params = { format = "json" }
```

## `[response]`

Controls how the upstream HTTP response is mapped back to monet. Used only with `type = "http"`.

| Field | Type | Default | Description |
|---|---|---|---|
| `output` | `str` | required for http | JSONPath to extract the output text |
| `artifacts` | `dict[str, str]` | `{}` | Named artifact paths. Keys are artifact names; values are JSONPath expressions. |

Example:

```toml
[response]
output = "$.message"
artifacts.report = "$.structured_output"
artifacts.code = "$.code_block"
```

## `[process]`

Subprocess management. Used when the adapter should launch and manage the upstream process.

| Field | Type | Default | Description |
|---|---|---|---|
| `command` | `list[str]` | `[]` | Command to run |
| `workdir` | `str \| null` | `null` | Working directory |
| `ready_timeout` | `int` | `120` | Seconds to wait for health check before serving |
| `env` | `dict[str, str]` | `{}` | Extra environment variables (supports `${VAR}`) |

The process's stderr passes through to the adapter's stderr. For `type = "http"`, stdout is also passed through. For `type = "stdio"`, stdin/stdout are piped for JSON-RPC communication.

Example:

```toml
[process]
command = ["npx", "tsx", "server.ts"]
workdir = "/pi"
ready_timeout = 60

[process.env]
PORT = "9000"
OPENAI_API_KEY = "${NVIDIA_NIM_API_KEY}"
```

## `[headers]`

Extra HTTP headers sent with every upstream request. Works with `type = "openai"` and `type = "http"`.

```toml
[headers]
X-Api-Version = "2"
Authorization = "Bearer ${KEY}"   # overrides top-level auth
```

## `[stdio]`

Configuration for `type = "stdio"` adapters.

| Field | Type | Default | Description |
|---|---|---|---|
| `command` | `list[str]` | required | Command to launch with stdin/stdout pipes |
| `plugin` | `str` | required | Plugin import path (`"module:function"`) |
| `init_rpc` | `str \| null` | `null` | RPC method to call once on startup (e.g. `"initialize"`) |

The plugin function signature:

```python
def run_task(rpc: Callable[[str, dict], dict], message: str) -> str:
    """
    rpc: sends JSON-RPC 2.0 request, blocks until result.
         Streaming notifications are in result["_streamed"].
    message: task string from payload.task
    returns: output text
    """
```

## Environment variable interpolation

All string fields support:

- `${VAR}` — substitute environment variable `VAR`; raises `KeyError` if not set
- `${VAR:default}` — substitute `VAR`, fall back to `default` if not set

Interpolation is applied recursively to all string values after TOML parsing, including nested dicts and `[process.env]`.

## Type requirements

| Type | Required fields |
|---|---|
| `openai` | `url` |
| `http` | `url`, `[response].output` |
| `stdio` | `[stdio].command`, `[stdio].plugin` |
| `plugin` | `plugin` (top-level) |

## Complete example

```toml
name = "pi"
type = "http"
url = "http://localhost:9000/chat"
health = "/health"
timeout = 300
ready_timeout = 120

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
