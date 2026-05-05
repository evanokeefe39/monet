# Adapter Protocol

An **adapter** is a small HTTP server that wraps an agent process and translates
between the monet worker and whatever protocol the agent natively speaks (chat,
stdio, gRPC, etc.).  Every adapter must implement two endpoints: `/health` and
`/task`.

## Startup sequence

The adapter is responsible for its own initialization.  The monet worker polls
`/health` before sending any `/task` requests, so the adapter must:

1. Start the underlying agent process (or establish whatever connection is needed).
2. Wait until the agent is fully ready — model loaded, network connections
   established, workspace directories created.
3. Begin serving `/health` returning `200` only after step 2 completes.

`/health` must not return `200` until the adapter can accept a `/task` request
and successfully route it to the agent.  An adapter that starts the HTTP server
before its agent is ready will pass health checks and then fail on the first
task — this is the most common adapter bug.

## `/health` — liveness and readiness

```
GET /health
```

**Response — ready:**

```
HTTP/1.1 200 OK
Content-Type: application/json

{"ok": true}
```

**Response — not ready / degraded:**

```
HTTP/1.1 503 Service Unavailable
Content-Type: application/json

{"ok": false}
```

The worker retries `/health` until it receives `200` before claiming tasks for
this pool.  If the agent process dies, the adapter should return `503` on
subsequent health checks so the worker can detect the failure.

## `/task` — task execution

```
POST /task
Content-Type: application/json
```

### Request body

Defined by `monet.worker.transport.AdapterTaskRequest`:

```json
{
  "task_id": "string",
  "payload": {
    "task": "string",
    "...": "..."
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | yes | Unique identifier for this task execution. |
| `payload` | object | yes | Task-specific data. Adapters typically read `payload.task` or `payload.command` as the primary instruction. |

### Success response — 200

Defined by `monet.worker.transport.AdapterTaskResponse`:

```json
{
  "output": "string",
  "success": true,
  "artifacts": {
    "key": "string content"
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `output` | string | yes | Primary text result from the agent. |
| `success` | bool | yes | Must be `true` on the 200 path. |
| `artifacts` | object | no | Named string payloads to surface as artifacts. Keys are artifact names; values are string content. |

### Error response — 4xx / 5xx

Defined by `monet.worker.transport.AdapterErrorResponse`:

```json
{
  "error": "human-readable description",
  "error_code": "AGENT_ERROR"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `error` | string | yes | Human-readable error description. |
| `error_code` | string | yes | Machine-readable code (see table below). |

**`error_code` values:**

| Code | HTTP status | Meaning |
|---|---|---|
| `INVALID_REQUEST` | 400 | Request body missing or malformed. |
| `AGENT_ERROR` | 500 | Agent/backend raised an exception. |
| `UPSTREAM_ERROR` | 502 | Adapter's upstream dependency (e.g. LLM provider) returned a non-2xx response. |
| `NOT_READY` | 503 | Adapter received a task before initialization completed. Should not occur if `/health` is polled correctly. |

## Error propagation

The worker deserializes the error response and raises `AgentError` with:

- `str(exc)` — `HTTP {status} [{error_code}]: {error}`
- `exc.status_code` — the HTTP status code
- `exc.body` — the full raw response body

Callers that catch `AgentError` can inspect `.status_code` and `.body` without
re-parsing the string message.  The full response body is also logged at `DEBUG`
level before the exception is raised, so `--log-level debug` is sufficient to
diagnose adapter errors without modifying adapter code.

## Writing a new adapter

1. Copy `examples/agent-adapters/pi/adapter.py` as a starting point.
2. Replace the Pi subprocess launch with your agent's startup logic.
3. Make `/health` return `200` only after your agent passes its own readiness
   check.
4. Translate `/task` request → agent call → `/task` response, including
   `error_code` on all error paths.
5. Verify with `curl`:

```bash
# Health check
curl http://localhost:8080/health

# Task request
curl -X POST http://localhost:8080/task \
  -H 'Content-Type: application/json' \
  -d '{"task_id": "test-1", "payload": {"task": "echo hello"}}'
```
