# CLAUDE.md

## Project

monet is a multi-agent orchestration SDK for Python. MIT licensed, solo maintainer. The SDK provides an `@agent` decorator, typed context injection, a catalogue/artifact system, orchestration via LangGraph, and a FastAPI server layer. Keep things minimal and clean.

## Layout

- `src/monet/` — package source (src layout)
  - `_decorator.py` — `@agent` decorator (core public API)
  - `_types.py` — `AgentResult`, `AgentRunContext` and related types
  - `_context.py` — `contextvars`-based run context (`get_run_context`, `get_run_logger`)
  - `_registry.py` — agent/command registration
  - `_stubs.py` — `emit_progress`, `write_artifact`, `set_catalogue_client`
  - `_tracing.py` — OpenTelemetry integration
  - `descriptors.py` — capability descriptors
  - `exceptions.py` — `SemanticError`, `EscalationRequired`, `NeedsHumanReview`
  - `catalogue/` — artifact catalogue: index, memory, metadata, storage, protocol, service
  - `orchestration/` — LangGraph orchestration: state, node wrapper, invoke, retry, content limits
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