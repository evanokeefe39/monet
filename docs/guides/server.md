# Server & Transport

monet includes a FastAPI server that exposes agents and the catalogue over HTTP. The agent interface is transport-agnostic -- agents run as local Python functions or over HTTP with no code changes.

## Application factory

```python
from monet.server import create_app
from monet.catalogue import CatalogueService, FilesystemStorage

# Without catalogue
app = create_app()

# With catalogue
storage = FilesystemStorage(root="/tmp/monet-catalogue")
service = CatalogueService(storage=storage, db_url="sqlite:///catalogue.db")
app = create_app(catalogue_service=service)
```

Run with uvicorn:

```bash
uvicorn myapp:app --reload
```

## Routes

### Health

```
GET /health
```

Returns `{"status": "ok"}`.

### Agent invocation

```
POST /agents/{agent_id}/{command}
```

Request body:

```json
{
    "task": "Research quantum computing trends",
    "command": "deep",
    "effort": "high",
    "trace_id": "trace-abc-123",
    "run_id": "run-456"
}
```

The server looks up the handler in the agent registry, constructs an `AgentRunContext`, calls the handler, and returns the `AgentResult` as JSON.

### Artifact write

```
POST /artifacts
```

Body: raw bytes. Metadata via headers:

| Header | Description |
|---|---|
| `Content-Type` | MIME type of the artifact |
| `x-monet-summary` | Text summary |
| `x-monet-created-by` | Agent name |
| `x-monet-trace-id` | OTel trace ID |
| `x-monet-run-id` | LangGraph run ID |

Returns `{"artifact_id": "...", "url": "..."}`.

### Artifact read

```
GET /artifacts/{artifact_id}
```

Returns the artifact content with the appropriate `Content-Type`.

### Artifact metadata

```
GET /artifacts/{artifact_id}/meta
```

Returns the metadata sidecar as JSON.

!!! note
    Catalogue routes return 501 if no `CatalogueService` was provided to `create_app()`.

## Deployment model

The initial deployment is a single FastAPI server hosting all agents and the orchestrator as co-located services. Each agent is a callable invoked as a direct Python function call within the same process.

When an agent needs independent scaling, it moves to its own service. The only change is environment configuration:

- Set `MONET_AGENT_TRANSPORT=http`
- Set `MONET_AGENT_{AGENT_ID}_URL=http://agent-service:8000`

The agent interface, the graph, and agent internals are untouched. The node wrapper handles both local and HTTP invocation transparently.

When a Python SDK agent moves to a separate service, its FastAPI endpoint extracts the OTel `traceparent` header, activates it as the OTel context, sets the `AgentRunContext` from the envelope fields, and calls the decorated function directly. The decorator behaves identically to the co-located case.
