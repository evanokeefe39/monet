# Server API Reference

## Python API

### `create_app`

```python
from monet.server import create_app

def create_app(
    catalogue_service: CatalogueService | None = None,
) -> FastAPI
```

Creates a FastAPI application with agent, catalogue, and health routes.

- If `catalogue_service` is provided, catalogue routes are enabled
- If not provided, catalogue routes return 501

## HTTP endpoints

### `GET /health`

Returns server health status.

**Response:**
```json
{"status": "ok"}
```

### `POST /agents/{agent_id}/{command}`

Invoke an agent.

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `task` | string | yes | Natural language instruction |
| `command` | string | no | Command name (echoed from URL) |
| `effort` | string | no | `"low"`, `"medium"`, or `"high"` |
| `trace_id` | string | yes | OTel trace ID |
| `run_id` | string | yes | LangGraph run ID |

**Response:** `AgentResult` serialised as JSON.

### `POST /artifacts`

Write an artifact to the catalogue.

**Request body:** Raw bytes (the artifact content).

**Headers:**

| Header | Required | Description |
|---|---|---|
| `Content-Type` | yes | MIME type |
| `x-monet-summary` | no | Text summary |
| `x-monet-created-by` | no | Agent name |
| `x-monet-trace-id` | no | OTel trace ID |
| `x-monet-run-id` | no | LangGraph run ID |

**Response:**
```json
{"artifact_id": "uuid-here", "url": "file:///path/to/artifact"}
```

### `GET /artifacts/{artifact_id}`

Read artifact content.

**Response:** Raw bytes with the artifact's `Content-Type`.

### `GET /artifacts/{artifact_id}/meta`

Read artifact metadata.

**Response:** Metadata as JSON dict (all `ArtifactMetadata` fields).
