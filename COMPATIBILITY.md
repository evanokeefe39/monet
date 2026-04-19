# Wire Compatibility Reference

Documents the wire protocol between the monet Python server and the Go
`monet-tui` binary. Both sides must conform to this contract. Changes here
require updating `tests/compat/wire_schema.json` and passing both
`tests/compat/test_wire_compat.py` and `go/tests/contract/wire_compat_test.go`.

## Version negotiation

On startup `monet-tui` calls `GET /api/v1/health` and checks `version` against
the `ServerVersionMin`/`ServerVersionMax` range compiled into the binary
(see `go/internal/config/version.go`). A major-version mismatch is a hard
failure with an actionable message.

Current range: `[0.1.0, 1.999.999]`

## SSE stream — run events

All events arrive as Server-Sent Events on `POST /api/v1/runs/stream`.
The `event:` field identifies the type; the `data:` field is JSON.

| SSE `event:` | JSON type | Required fields |
|---|---|---|
| `run_started` | `RunStarted` | `run_id`, `graph_id`, `thread_id` |
| `updates` | `NodeUpdate` | `run_id`, `node`, `update` |
| `custom` (progress) | `AgentProgress` | `run_id`, `agent`, `status` |
| `custom` (signal) | `SignalEmitted` | `run_id`, `agent`, `signal_type` |
| `interrupt` | `Interrupt` | `run_id`, `tag` |
| `end` | `RunComplete` | `run_id` |
| `error` | `RunFailed` | `run_id`, `error` |

### Notes

- `AgentProgress` and `SignalEmitted` share the same `custom` SSE event type.
  Consumers distinguish them by the presence of `signal_type` (signal) vs its
  absence (progress).
- `agent` is the JSON key for the agent identifier in both `AgentProgress` and
  `SignalEmitted` (not `agent_id`).
- All optional fields are omitted when empty (not null).

## REST responses

### `GET /api/v1/health`

```json
{
  "status": "ok",
  "workers": 2,
  "queued": 0,
  "version": "0.5.0",
  "queue_backend": "redis_streams",
  "uptime_seconds": 3600.0
}
```

Required: `status`, `workers`, `queued`.

### `GET /api/v1/agents`

Returns a JSON array. Each entry:

```json
{
  "agent_id": "planner",
  "command": "plan",
  "description": "...",
  "pool": "local",
  "worker_id": "w1"
}
```

Required: `agent_id`, `command`.

### `GET /api/v1/artifacts`

```json
{
  "artifacts": [
    {
      "artifact_id": "...",
      "key": "work_brief",
      "content_type": "application/json",
      "content_length": 1024,
      "summary": "...",
      "created_at": "2026-01-01T00:00:00Z"
    }
  ],
  "next_cursor": null
}
```

Required per item: `artifact_id`, `key`.

## Interrupt form convention

When a graph calls `interrupt(value)` with a value that has `prompt` and
`fields` keys, `monet-tui` renders a form. The `fields` array contains objects
with at least `name` and `type`. Supported `type` values: `text`, `textarea`,
`radio`, `checkbox`, `select`, `select_or_text`, `int`, `bool`, `hidden`.

Graphs that don't follow this convention produce a raw JSON display.

## Schema source of truth

`tests/compat/wire_schema.json` is the machine-readable version of the
required-fields table above. Both CI jobs read it.
