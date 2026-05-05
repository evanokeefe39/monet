# Adapter SDK — Implementation Plan

Spec: `docs/architecture/adapter-sdk-spec.md`

Goal: config-based agent onboarding. Replace 150-180 line boilerplate adapters with
TOML config (3-16 lines) + optional plugin (5-10 lines). Single CLI command serves.

---

## Phase 1: Foundation (`src/monet/adapter/`)

Core modules everything depends on. No external deps needed (tomllib is stdlib 3.11+).

- [x] `_errors.py` — `AdapterError(message, code)` with JSON serialization matching wire contract `{"error": str, "error_code": str}`
- [x] `_env.py` — `${VAR}` and `${VAR:default}` interpolation. Regex-based, applied to all string fields post-parse.
- [x] `_jsonpath.py` — minimal dot-access: `$.field.nested`, `$.arr[0]`. Two operations: `extract(obj, path) -> Any`, `assign(obj, path, value) -> obj`. No query filters.
- [x] `_config.py` — TOML parsing + Pydantic model:
  - Top-level: `name`, `type`, `url`, `port=8080`, `timeout=300`, `health`, `model`, `auth`, `ready_timeout=120`, `command`
  - `[request]`: `body: dict`, `params: dict`, `method: str = "POST"`
  - `[response]`: `output: str`, `artifacts: dict[str, str]`
  - `[process]`: `command: list[str]`, `workdir: str | None`, `ready_timeout: int = 120`, `env: dict[str, str]`
  - `[headers]`: `dict[str, str]`
  - `[stdio]`: `command: list[str]`, `plugin: str`, `init_rpc: str | None`
  - Validation: type=openai/http requires url, type=http requires response.output, type=stdio requires stdio.command+plugin
  - `load_config(path: Path) -> AdapterConfig` — read TOML, interpolate env, validate, return
- [x] `_types.py` — shared `TaskRequest`, `TaskResponse`, `ProxyBackend` Protocol
- [x] `__init__.py` — re-export `serve`, `AdapterError`, `load_config`

---

## Phase 2: Health + Process Management

- [x] `_health.py`:
  - `HealthCascade` — ordered list of check strategies built from config
  - For openai type: try `{url}/health` → `{url}/v1/models` → TCP
  - For http type: try `{url}{health_path}` → TCP
  - For stdio/plugin: process alive check
  - `check_health(cascade) -> bool` — first success wins
  - `wait_healthy(cascade, timeout) -> None` — poll 1s interval, raise on timeout/early-exit
  - Uses httpx with 2s per-request timeout

- [x] `_process.py`:
  - `start_process(config: ProcessConfig) -> subprocess.Popen` — start with env, workdir, stderr passthrough
  - `stop_process(proc) -> None` — SIGTERM, 5s grace, SIGKILL. Windows: taskkill /F /T /PID
  - No stdin/stdout capture for http types (just fire-and-forget subprocess)
  - Stdin/stdout pipes only for stdio type (handled in proxy_stdio)

---

## Phase 3: Proxy Backends

Shared interface in `_types.py`:

```python
class ProxyBackend(Protocol):
    async def handle_task(self, request: TaskRequest) -> TaskResponse: ...
    async def close(self) -> None: ...

@dataclass
class TaskRequest:
    task_id: str
    task: str
    payload: dict[str, Any]

@dataclass
class TaskResponse:
    output: str
    artifacts: dict[str, str]
```

- [x] `_proxy_openai.py`:
  - Build messages: `[{"role": "user", "content": request.task}]`
  - URL: if no path or ends `/v1` → append `/v1/chat/completions`
  - Auth: `config.auth` → `$OPENAI_API_KEY` → none
  - Model: include if `config.model` set, omit otherwise
  - Extract: `choices[0].message.content`
  - Non-streaming only for v1 (simplest correct thing)
  - httpx.AsyncClient, timeout from config

- [x] `_proxy_http.py`:
  - Build body: recursively resolve `$.path` values in template body dict via jsonpath
  - Append `request.params` as query string
  - Send with `request.method` (default POST)
  - Add `config.headers`
  - Extract `response.output` via jsonpath from upstream response
  - Extract `response.artifacts.*` via jsonpath

- [x] `_proxy_stdio.py`:
  - Start subprocess from `stdio.command` with stdin/stdout pipes
  - Send `init_rpc` if configured
  - Import plugin function from `stdio.plugin` (format: `module:function`)
  - On each task: call `plugin_fn(rpc, request.task)` where `rpc(method, params) -> dict`
  - RPC implementation: JSON-RPC 2.0 over stdin/stdout, accumulate notifications in `_streamed`
  - Thread lock for serialization (subprocess is single-threaded)

- [x] `_proxy_plugin.py`:
  - Import function from config (format: `module:function`)
  - Call `handle_task(task_id, payload) -> {"output": ..., "artifacts": ...}`
  - Wrap exceptions in AdapterError

---

## Phase 4: Server Layer

- [x] `_server.py` — FastAPI app:
  - `GET /health` → run health cascade, return `{"ok": bool}`
  - `POST /task` → parse body `{task_id, payload: {task, ...}}`, dispatch to backend, return `{output, artifacts?, success}`
  - Error responses: 400 INVALID_REQUEST, 502 UPSTREAM_ERROR, 500 AGENT_ERROR, 504 TIMEOUT, 503 NOT_READY
  - Lifespan: start process → wait ready → serve; on shutdown → stop process, close backend
  - `create_app(config: AdapterConfig) -> FastAPI` — factory function
  - `serve(config_path: Path, host="0.0.0.0", port=None) -> None` — load config, create app, run uvicorn

---

## Phase 5: CLI

- [x] `src/monet/cli/_adapter.py`:
  ```
  @click.group()
  def adapter(): ...

  @adapter.command()
  @click.argument("config_path")
  def serve(config_path): ...     # load + serve

  @adapter.command()
  @click.argument("config_path")
  def check(config_path): ...     # validate + print summary

  @adapter.command()
  @click.option("--type", default="openai")
  def init(type): ...             # print starter TOML

  @adapter.command()
  @click.argument("url")
  def ping(url): ...              # hit /health + /task with test payload
  ```
- [x] Register `adapter` group in `cli/__init__.py`

---

## Phase 6: Example Configs

- [x] `examples/agent-adapters/configs/hermes.toml`
- [x] `examples/agent-adapters/configs/openclaw.toml`
- [x] `examples/agent-adapters/configs/pi.toml`
- [x] `examples/agent-adapters/configs/zeroclaw.toml`
- [x] `examples/agent-adapters/configs/zeroclaw_plugin.py`
- [x] Deprecation note in existing adapter.py files (one-line comment at top)

---

## Phase 7: Docker + Docs

- [x] `docker/adapter.Dockerfile`
- [x] `docs/guides/adapter-sdk.md` — onboarding guide
- [x] `docs/reference/adapter-config.md` — full config reference
- [x] Update `mkdocs.yml` nav

---

## Phase 8: Tests

- [x] `tests/test_adapter_env.py` — interpolation: basic, default, missing, nested
- [x] `tests/test_adapter_jsonpath.py` — extract flat/nested/array, assign new/nested
- [x] `tests/test_adapter_config.py` — parse minimal/full/invalid, defaults, validation errors
- [x] `tests/test_adapter_health.py` — cascade order, timeout, TCP fallback (mock httpx)
- [x] `tests/test_adapter_proxy_openai.py` — request build, auth inference, URL normalization, response extraction
- [x] `tests/test_adapter_proxy_http.py` — body mapping, params, response extraction, artifacts
- [x] `tests/test_adapter_server.py` — TestClient: /health, /task happy path, error codes

---

## Open Questions

1. **Plugin field location**: `type=plugin` needs a plugin path. Spec uses `[stdio].plugin` for stdio type. Proposal: add top-level `plugin = "mod:fn"` for `type=plugin`. Keep `[stdio].plugin` for `type=stdio`. Different fields, same import mechanism. ✓ Implemented as specified.

2. **Streaming in v1**: Current adapters consume streaming internally, return final text. Spec mentions "handles SSE streaming transparently" for openai type. Proposal: v1 does non-streaming POST only. v2 adds streaming pass-through if needed. ✓ Non-streaming implemented.

3. **Async vs sync proxy for stdio**: subprocess stdin/stdout is blocking I/O. Proposal: wrap in `asyncio.to_thread` so FastAPI stays async. Plugin functions remain sync (simpler for users). ✓ Implemented with asyncio.to_thread.

---

## Dependency Check

All deps already in pyproject.toml:
- `tomllib` — stdlib
- `httpx` — present
- `fastapi` + `uvicorn` — present
- `pydantic` — present
- `click` — present

Zero new deps.

---

## Implementation Order

```
Phase 1 (foundation)
  ↓
Phase 2 (health + process)
  ↓
Phase 3 (proxy backends — can parallelize all 4)
  ↓
Phase 4 (server wires proxies together)
  ↓
Phase 5 (CLI — thin glue)
  ↓
Phase 6, 7, 8 (examples, docker, tests — parallel)
```

Phase 7 (Docker + Docs) deferred.
