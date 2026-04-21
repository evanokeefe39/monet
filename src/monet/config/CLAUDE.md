# monet.config — Configuration Models

## Responsibility

Pydantic v2 models for all process config surfaces. No global singletons — callers load and pass config explicitly.

## Models

| Model | Used by |
|-------|---------|
| `ServerConfig` | `monet server` / `aegra serve` |
| `WorkerConfig` | `monet worker` |
| `ClientConfig` | `MonetClient` |
| `CLIDevConfig` | `monet dev` / `monet run` / `monet chat` |
| `QueueConfig` | embedded in `ServerConfig` + `WorkerConfig` |
| `ObservabilityConfig` | embedded in `ServerConfig` |
| `ArtifactsConfig` | embedded in `ServerConfig` |
| `AuthConfig` | embedded in `ServerConfig` + `WorkerConfig` |
| `ChatConfig` | embedded in `ServerConfig` |
| `OrchestrationConfig` | embedded in `ServerConfig` |

## Key rules

- Server and worker config are separate — they deploy independently. Do not couple them.
- No ENV-mode toggle vars (`MONET_ENV=production`, `MODE=dev`). Each behavior gets its own explicit named knob. Boot validation rejects missing required values.
- `QueueConfig.validate_for_boot()` rejects unknown backend names and missing credentials. Memory backend rejected at boot when `REDIS_URI` is set.
- `ObservabilityConfig.otlp_endpoint_and_headers()` resolves OTLP target from vendor shortcuts (Langfuse, LangSmith, Honeycomb) without mutating `os.environ`.
- `WorkerConfig` is distinct from `ServerConfig` — remote-mode worker needs `server_url` + `api_key`; local sidecar needs neither.
