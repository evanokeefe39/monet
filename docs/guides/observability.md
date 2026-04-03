# Observability

OpenTelemetry is a hard dependency. The SDK imports and configures it unconditionally. Agents that run without an OTel collector configured emit spans to a no-op exporter -- this is the correct degraded behaviour, not an error.

## Instrumentation levels

Spans fire at two levels:

**Intra-agent** -- from within the `@agent` decorator. Every agent invocation gets an OTel span with `gen_ai.*` semantic conventions. Attributes include agent ID, command, effort, run ID, and trace ID.

**Inter-agent** -- from the LangGraph node wrapper. `create_node()` starts a span before calling the agent and ends it after the result is received. Applied uniformly across all agents regardless of runtime.

## Structured logging

`get_run_logger()` returns a logger pre-populated with context from the current agent invocation:

```python
from monet import agent, get_run_logger

@agent(agent_id="researcher")
async def researcher(task: str):
    """Research a topic."""
    logger = get_run_logger()
    logger.info("Starting research", extra={"task_length": len(task)})
    result = await do_research(task)
    logger.info("Research complete", extra={"result_length": len(result)})
    return result
```

The logger injects `trace_id`, `run_id`, `agent_id`, and `command` into every log record via a `LoggerAdapter`. Outside the decorator, a no-op logger is returned.

## Trace continuity

W3C `traceparent` headers are propagated across service boundaries. In the co-located deployment, OTel context propagation is automatic across Python async tasks. When agents become separate services, the node wrapper injects `traceparent` explicitly before HTTP calls. The agent's FastAPI endpoint extracts and activates it.

The SDK provides utilities for trace header management:

- `format_traceparent()` -- creates a W3C traceparent header string
- `parse_traceparent()` -- parses a traceparent header
- `inject_traceparent()` -- injects the header for outbound HTTP calls

These are internal utilities used by the node wrapper and server. Agent authors typically do not need to call them directly.

## Configuration

Point the OTel exporter at your collector:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

## Langfuse

Langfuse is the recommended observability backend. It is self-hosted and receives OTel traces via OTLP over HTTP. Extended span attributes (agent ID, run ID, command, effort, signals) appear as trace metadata in the Langfuse dashboard.

Cost and latency are captured in OTel traces. They are not duplicated in the output envelope. Over time, actuals can be compared against declared SLA characteristics in capability descriptors for continuous improvement.
