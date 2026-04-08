# CLAUDE.md

## Project

monet is a multi-agent orchestration SDK for Python. MIT licensed, solo maintainer. The SDK provides an `@agent` decorator, typed context injection, a catalogue/artifact system, orchestration via LangGraph, and a FastAPI server layer. Keep things minimal and clean.

## Layout

- `src/monet/` — package source (src layout)
  - `_decorator.py` — `@agent` decorator (core public API). Dual call form: `agent("id")` returns a partial, `@agent(agent_id=..., command=...)` is the verbose form.
  - `types.py` — `AgentResult` (output: `str | dict | None`), `AgentRunContext`, `Signal`, `ArtifactPointer`
  - `signals.py` — `SignalType` vocabulary + `BLOCKING`/`RECOVERABLE`/`INFORMATIONAL`/`AUDIT`/`ROUTING` frozensets. Orchestrator routes on group membership, never raw strings.
  - `streams.py` — `AgentStream`: typed async event bus for external agents. `.cli()`, `.sse()`, `.http()` constructors, `.on()` handler builder, `.run()` execution. Subclass and override `_iter_events()` for transports beyond the bundled set (e.g. gRPC).
  - `handlers.py` — handler factories for `AgentStream.on()`: `webhook_handler`, `log_handler`
  - `_context.py` — `contextvars`-based run context (`get_run_context`, `get_run_logger`)
  - `_registry.py` — agent/command registration
  - `_stubs.py` — ambient trio: `emit_progress`, `emit_signal`, `write_artifact`
  - `_catalogue.py` — `get_catalogue()` handle; `write()` appends pointers to the decorator's artifact collector
  - `_tracing.py` — OpenTelemetry integration
  - `descriptors.py` — capability descriptors
  - `exceptions.py` — `SemanticError`, `EscalationRequired`, `NeedsHumanReview`
  - `agents/` — reference agents (planner, researcher, writer, qa, publisher) using the partial form
  - `catalogue/` — artifact catalogue: index, memory, metadata, storage, protocol, service
  - `orchestration/` — LangGraph orchestration: state, node wrapper, invoke, three graph builders (`entry`, `planning`, `execution`), `_validate._assert_registered` for build-time poka-yoke
  - `server/` — FastAPI app: agent routes, catalogue routes, health
- `tests/` — pytest test directory
- `docs/` — mkdocs-material documentation

## Commands

```bash
uv sync --group dev    # install all dependencies
uv run pytest          # run tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy src/       # type check
uv run mkdocs serve    # local docs preview
```

## Dependencies

Runtime: pydantic, opentelemetry-api/sdk, sqlalchemy, fastapi, uvicorn, langgraph.
Dev: pytest, pytest-asyncio, hypothesis, httpx, ruff, mypy, mkdocs-material, pre-commit.

## Code standards

- Python 3.12+, type annotations on all public API
- `py.typed` marker is present — maintain inline types
- mypy strict mode, zero errors required
- ruff for linting and formatting, line length 88
- Pre-commit hooks enforce ruff and mypy before each commit

## Testing

- All tests in `tests/` using pytest, async mode auto
- Test files named `test_*.py`
- Every public function needs a corresponding test

## Style

- src layout, all imports from `monet`
- No unnecessary abstractions or speculative code
- Keep dependencies minimal — justify any new addition
- Docstrings on public API only, Google style

## CI

GitHub Actions runs ruff, mypy, and pytest on push/PR to main. All checks must pass.

## Do not

- Add dependencies without explicit approval
- Modify CI workflow without explicit approval
- Create files outside the established layout
- Add compatibility shims or backwards-compat code

## Architecture

System design is outlined in SPEC.md. Key design principles: agents are opaque capability units with a uniform interface, the orchestrator owns routing and HITL policy, OpenTelemetry observability is non-negotiable, and context engineering (controlling exactly what the model sees) is prioritized over prompt gymnastics.

Decorator/stream/orchestrator division of labour:
- The `@agent` decorator is registration + context injection only. It does not detect transports or make execution decisions. It calls the function, awaits it, wraps the result, handles exceptions.
- `AgentStream` is the translation boundary between an external agent's output and the SDK primitives. Handlers are registered at the point of use inside the function body via `.on()`. There is no agent-level handler registration.
- The orchestrator reads `AgentResult.output` (inline string/dict/None) and `AgentResult.artifacts` (catalogue pointers) as distinct fields. There is no fallback between them.
- Registry validation happens at the earliest detectable point: required agents are checked when `build_entry_graph()` / `build_planning_graph()` / `build_execution_graph()` run; planner-specified agents are checked in `fan_out_wave` and surface as `SemanticError` so HITL can respond.
- Large string returns from `@agent` functions are auto-offloaded to the catalogue when content exceeds `DEFAULT_CONTENT_LIMIT`. The pointer lands in `artifacts`; `output` becomes a 200-char inline summary.