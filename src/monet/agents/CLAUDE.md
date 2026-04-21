# monet.agents — Reference Agents

## Responsibility

Five reference agents ship with monet: `planner`, `researcher`, `writer`, `qa`, `publisher`. Plus `evaluator`. These are opaque capability units — orchestrator routes to them, does not inspect their output.

## Registration

`register_reference_agents()` — idempotent, re-registers all five into `default_registry`. Call in tests after registry rollback. Plain `import monet.agents` is a no-op after first load (sys.modules).

## File structure

Each agent: thin `__init__.py` wrapper + zero monet imports in logic modules. Logic lives in per-agent modules. `_prompts.py` holds shared prompt templates.

## What agents own

- LLM provider imports (direct, no shared model factory)
- Their own output validation
- Emitting signals via `emit_signal()` for non-fatal conditions
- Raising exceptions for fatal conditions

## What agents do NOT own

- Orchestration routing
- HITL policy
- Artifact storage (they call `write_artifact()`, don't touch store directly)
- Quality enforcement (QA agent is the semantic layer)

## Invariants

- Agents are untrusted black boxes from orchestrator's perspective
- No `models.py` or shared model factory — each agent imports its LLM provider directly
- Planner-friendly docstrings required so planner can route correctly
- New capabilities = new reference agent. Custom graphs only for novel orchestration topologies.
