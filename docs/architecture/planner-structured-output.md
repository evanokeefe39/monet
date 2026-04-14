# Planner Structured Output — Exploration

**Status:** exploratory. Not a committed migration.
**Trigger to execute:** a recurrence of planner JSON-parse failures, or a deliberate push to raise reliability floor of reference agents. If neither is active, defer.
**Scope:** swap the planner's "ask LLM for JSON, strip fence, `json.loads`, `pydantic.validate`" pipeline for a structured-output approach with validation-retry built in.

## Current state

`src/monet/agents/planner/__init__.py` has two commands:

- `planner/fast` (triage) — returns raw JSON text after fence-stripping; no schema validation.
- `planner/plan` — renders Jinja prompt, calls `init_chat_model(...).ainvoke([...])`, passes the string through `_strip_json_fence`, `json.loads`, then `WorkBrief.model_validate`. Any failure raises `ValueError` and the run fails.

Failure modes that exist today:
- Model wraps JSON in a non-standard fence (`~~~`, prose preamble, trailing commentary) → `_strip_json_fence` misses it → `json.loads` errors.
- Model omits a required `WorkBrief` field → `ValidationError` bubbles up as `ValueError`, no retry.
- Model emits a field with a near-right but not-right type (e.g. `depends_on: "a,b,c"` instead of `["a", "b", "c"]`) → pydantic rejects it, same outcome.
- No self-correction loop. Each of these is a single-shot failure → dead run.

The gap: there is no structured-output contract between the model and the planner. The model is trusted to emit well-formed JSON matching `WorkBrief` in one shot, and when it doesn't, there is no retry-with-feedback step.

## Candidate approaches

Three realistic options. User framed this as "pydantic-ai"; listing alternatives so the decision is deliberate.

### A. langchain `.with_structured_output(WorkBrief)` — zero new deps

Already in the stack. `init_chat_model(...).with_structured_output(WorkBrief)` returns a Runnable that uses the provider's tool-calling / JSON-mode feature under the hood and returns a `WorkBrief` instance directly. Validation happens inside the Runnable. Supports `method="function_calling"` (OpenAI, Gemini, Anthropic tool-calling) and `method="json_mode"` fallback.

- **Pros:** no new dependency; already-used library; minimal diff.
- **Cons:** retry-on-validation-failure is not built in at the LangChain level — if the model emits a payload that fails validation, the call errors, same as today. Have to layer `with_retry(...)` or a custom retry loop on top. Retry feedback (telling the model *what* went wrong) is not native — you have to thread the validation error back into the prompt yourself.
- **Effort:** small.

### B. pydantic-ai — new dep, native structured-output + validation-retry

pydantic-ai (`pydantic_ai.Agent`) is purpose-built for this pattern. `Agent(result_type=WorkBrief)` returns a validated `WorkBrief` instance. Validation-retry with feedback is built in: on `ValidationError`, pydantic-ai injects the error message back as a model message and retries up to a configured limit. Typed tools, typed dependencies, usage tracking.

- **Pros:** the retry-with-feedback loop is the thing we actually want. Small API surface. Type-safe end to end. Owned by the pydantic team (same folks behind our validation layer).
- **Cons:** new dependency. Another "agent framework" in the stack alongside LangGraph and LangChain. Smaller ecosystem for tool integrations than LangChain. The `Agent` abstraction overlaps conceptually with monet's own `@agent` decorator — we'd be using pydantic-ai *inside* a monet agent, which is fine but worth calling out as a layering choice.
- **Effort:** small-to-medium. Pattern is well-documented; the dependency approval is the real friction.

### C. `instructor` — new dep, opinionated structured-output

`instructor` patches an OpenAI/Anthropic client to accept a `response_model` and does validate-then-retry natively. Similar shape to pydantic-ai but more provider-coupled and less agent-oriented.

- **Pros:** smallest API.
- **Cons:** orients around OpenAI/Anthropic clients rather than LangChain's provider abstraction — cuts against the current `init_chat_model` pattern.
- **Effort:** small, but the integration seam is worse.

## Recommendation

1. **Ship A first.** `with_structured_output(WorkBrief)` in `planner/plan` with an explicit retry loop (≤ 2 retries) that re-prompts on `ValidationError`/`OutputParserException` with the error message appended. Zero new dependency. Covers 80% of the failure surface. Matches the existing stack.
2. **Reconsider B later.** If (a) the retry-with-feedback loop in A gets non-trivial, (b) a second agent (e.g. qa, writer) needs the same pattern, or (c) LangChain's structured-output has a concrete limitation we hit — then the pydantic-ai dependency becomes proportional. Until then, the new dep is overproduction relative to the problem.
3. **Do not touch `planner/fast`.** Triage output is loose JSON that downstream consumes permissively. Introducing a schema there is a broader change with its own spec.

## Design sketch for A

```python
# src/monet/agents/planner/__init__.py (target state)

from langchain_core.exceptions import OutputParserException

async def _call_with_retry(
    model_string: str, prompt: str, *, max_retries: int = 2
) -> WorkBrief:
    model = _get_model(model_string).with_structured_output(WorkBrief)
    last_error: Exception | None = None
    current_prompt = prompt
    for attempt in range(max_retries + 1):
        try:
            result = await model.ainvoke([{"role": "user", "content": current_prompt}])
            return WorkBrief.model_validate(result)  # redundant but cheap; belt + suspenders
        except (ValidationError, OutputParserException) as exc:
            last_error = exc
            emit_progress({"status": "retrying plan", "agent": "planner", "attempt": attempt + 1})
            current_prompt = _env.get_template("plan_retry.j2").render(
                original_prompt=prompt,
                error=str(exc),
                attempt=attempt + 1,
            )
    assert last_error is not None
    raise ValueError(f"Planner failed to produce valid WorkBrief after {max_retries + 1} attempts: {last_error}") from last_error
```

A new template `plan_retry.j2` wraps the original prompt with an "the previous attempt failed validation with <error>; produce a corrected WorkBrief" preamble. Retry count and failure reason emitted as `emit_signal(AUDIT)` so operators can track reliability in Langfuse.

## Design sketch for B (if chosen later)

```python
# src/monet/agents/planner/__init__.py (target state, option B)
from pydantic_ai import Agent

planner_agent = Agent(
    model=_model_string(),
    result_type=WorkBrief,
    system_prompt="...",          # migrated from plan.j2
    result_retries=2,
)

@planner(command="plan")
async def planner_plan(task: str, context: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = await planner_agent.run(task, deps=...)   # returns WorkBrief, already validated
    work_brief = result.data
    ...
```

Cleanest code. Cost: one dependency, and conceptual overlap with `@agent`.

## Does this help wave/phase reliability?

User framing suggested structured output would yield a "reliable wave/phase plan". Be precise about what that does and doesn't:

- **Yes:** structured output eliminates the class of failures where the model emits prose, wraps the JSON unpredictably, or omits required fields. The plan you get is either a validated `WorkBrief` or a retry is triggered.
- **No:** structured output does not make the *semantic quality* of the plan better. A syntactically valid `WorkBrief` can still have wrong dependencies, the wrong agents chosen, or missing steps. That failure mode is addressed by (a) better prompting, (b) a post-plan review slot (see `graph-extension-points.md`'s `post_plan_review`), or (c) QA reflection on the execution output.
- "Reliable wave/phase plan" needs both layers — structured output is necessary but not sufficient. The `post_plan_review` slot, when it ships, is the place where a review loop catches semantic errors that structured output can't detect.

## Open questions

- Should `_strip_json_fence` be deleted when option A lands? It is only needed because the model emits fences around JSON; once `with_structured_output` is used, the parser never sees a fenced string. Lean delete.
- Retry budget: 2 retries is a guess. Log actual failure/retry rates for one month before fixing.
- Triage: `planner/fast` returns a dict with loose shape. Does it get a `TriageResult` pydantic model too? Separate decision; separate PR.
- Where do retry attempts show up in the run stream? Propose a `PlanRetry` event in `monet.pipelines.default.events` if retries become user-visible; otherwise keep them internal.

## Relationship to other deferred work

- **Reference agent quality pass** (`CLAUDE.md` → `### Lower priority / triggered`): this is one concrete improvement within that umbrella. Reliability of reference-agent output is one axis of that pass.
- **Graph extension points (slots) — `post_plan_review`** (`graph-extension-points.md`): solves the *semantic* validity of the plan. Structured output solves the *syntactic* validity. Complementary, not overlapping.
