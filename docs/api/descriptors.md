# Descriptors API Reference

All exports from `monet.descriptors`. These are static typed configurations loaded at startup, not runtime services.

## `AgentDescriptor`

```python
class AgentDescriptor(BaseModel):
    agent_id: str
    description: str = ""
    commands: dict[str, CommandDescriptor] = {}
    confidence_model: str = "self-reported"
```

Capability descriptor for an agent. Defines what commands it supports, their calling conventions, SLA characteristics, and retry semantics.

## `CommandDescriptor`

```python
class CommandDescriptor(BaseModel):
    calling_convention: Literal["sync", "async"] = "sync"
    effort_vocabulary: list[Effort] = ["low", "medium", "high"]
    sla: SLACharacteristics = SLACharacteristics()
    retry: RetryConfig = RetryConfig()
```

Descriptor for a single agent command. The `calling_convention` determines how the node wrapper calls the agent -- synchronous request-response or async with polling/SSE.

## `SLACharacteristics`

```python
class SLACharacteristics(BaseModel):
    expected_latency_ms: dict[str, int] = {}
    cost_tier: str = "standard"
```

Expected performance characteristics. `expected_latency_ms` maps effort levels to expected latency in milliseconds.

## `RetryConfig`

```python
class RetryConfig(BaseModel):
    max_retries: int = 3
    retryable_errors: list[str] = ["unexpected_error"]
    backoff_factor: float = 1.0
```

Retry semantics for a command. `retryable_errors` lists `SemanticError` type values that should trigger retry.

## `DescriptorRegistry`

```python
class DescriptorRegistry:
    def __init__(self) -> None
    def register(self, descriptor: AgentDescriptor) -> None
    def lookup(self, agent_id: str) -> AgentDescriptor | None
    def clear(self) -> None
    def registry_scope(self) -> Generator[None]
    def load_from_dict(self, data: dict[str, Any]) -> AgentDescriptor
```

Thread-safe registry for agent descriptors.

- `register()` -- stores a descriptor by `agent_id`
- `lookup()` -- returns descriptor or `None`
- `clear()` -- removes all descriptors
- `registry_scope()` -- context manager that snapshots and restores state for test isolation
- `load_from_dict()` -- constructs an `AgentDescriptor` from a dict, registers it, and returns it

## `default_descriptor_registry`

```python
default_descriptor_registry = DescriptorRegistry()
```

Module-level instance. Used by the system unless a custom registry is provided.
