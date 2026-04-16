# Researcher Migration — Current → GPT Researcher + Constrained Writer

**Status:** proposed migration, not implemented.
**Trigger to execute:** decision to treat reference agents as the shipped default that users copy. The current researcher is functional but produces unverifiable citations — a pattern users inherit when they copy it.
**Source of decision:** independent evaluation in `~/repos/agent-researcher` (April 2026), full report in `EVALUATION-REPORT.md` there. Seven open-source research agents evaluated across desk research, smoke tests, full benchmark (5 queries), citation verification, and token-consumption analysis.

## Evaluation summary (full report is the authority)

- **Winner: GPT Researcher** (Apache 2.0, 26K stars).
- **Decisive factor:** token consumption. GPT Researcher consumed ~11K tokens for a Q3 benchmark query vs ~2.07M for open_deep_research — a 190× ratio. GPT Researcher is search-first (1 LLM call to generate sub-questions, parallel search/scrape with no LLM in loop, 1 LLM call for synthesis). open_deep_research is LLM-in-the-loop (200+ LLM calls via ReAct with cumulative context). For an agent invoked repeatedly inside a multi-agent pipeline, 190× is the difference between viable and not.
- **Speed:** ~80s/query vs ~191s for open_deep_research.
- **Citation hallucination, original:** GPT Researcher scored 3.33/10 on citation integrity — its writer fabricated URLs (13 fake arXiv IDs with placeholder strings in one query).
- **Citation hallucination, fixed:** a universal fix — source registry + constrained writer — brought both GPT Researcher and open_deep_research to 10/10 citation integrity. The writer is given a registry of every URL the search API actually returned; the writer may cite only from that registry; a post-validator strips any citation not in the registry. This is the key architectural finding.
- **The writer matters as much as the research agent.** Same raw research fed to a constrained writer (with registry) vs. an unconstrained writer (typical built-in) changes the output from "impressive and unreliable" to "verifiable". Do not migrate the research step without also migrating the writer step.

## Current state (what ships today)

`src/monet/agents/researcher/__init__.py`:
- Two commands: `fast` (max 3 results) and `deep` (max 10 results).
- Provider order: Exa (`EXA_API_KEY` + `exa_py`) → Tavily (`TAVILY_API_KEY` + `langchain_tavily`). Exa path does search + `_format_exa_results` + LLM synthesis via Jinja template `deep_synth.j2`. Tavily path runs a `create_react_agent` ReAct loop.
- Quality gate: `MIN_RESEARCH_CONTENT_LENGTH = 500`. Below → `EscalationRequired`.
- Output: markdown artifact via `write_artifact`, confidence 0.7 (fast) / 0.85 (deep). Returned string is the raw synthesis.

Gaps:
- No source registry. URLs returned by the search API are folded into the synthesis prompt as text and never tracked separately.
- Writer step (LLM synthesis) is unconstrained — prompt asks for citations, model invents them from parametric knowledge when context is compressed. This matches the exact failure mode measured in the evaluation.
- No post-validation of citations against real sources.
- Tavily ReAct path has the same flaw plus the 190×-style token cost of ReAct loops — it is the worst combination of the two known failure modes.

## Target state

A three-step pipeline with a source registry as the contract between research and writing.

```
[researcher: gpt-researcher conduct_research]
        │ produces: raw findings + URLs/titles/snippets
        ▼
[SourceRegistry write]            ◄── artifact: keyed "source_registry"
        │ deduplicates, verifies (optional HTTP check)
        ▼
[writer: constrained synthesis]
        │ reads registry, drafts report, validates citations
        ▼
[RegistryValidator]
        │ strip/flag any citation not in registry
        ▼
artifact: report.md + citations traceable to registry
```

Concrete module changes:

### `src/monet/agents/researcher/` — swap search+synthesis for research-only

- Replace Exa / Tavily dual-provider logic with a single GPT Researcher call.
- Depend on `gpt-researcher` as an **optional** dependency group, matching the pattern already used for `exa-py` and `langchain-tavily` (module-level imports stay LLM-only so `import monet.agents` succeeds).
- Public surface of `researcher` stays: two commands (`fast`, `deep`), differing by GPT Researcher report-type / depth config, both return a summary string and write an artifact.
- New: researcher writes a second artifact alongside the main one — the source registry — keyed `"source_registry"`. Content: JSON `[{id, url, title, snippet, verified, ...}]`.
- Remove the Tavily ReAct path entirely. It is dominated by GPT Researcher on both cost and quality.
- Keep the `MIN_RESEARCH_CONTENT_LENGTH` gate and `EscalationRequired` behavior — these are the researcher's own quality contract and are correct.

### New: `src/monet/artifacts/_source_registry.py` (or similar)

- Typed wrapper over the artifact store for source-registry artifacts.
- API: `SourceRegistry.write(run_id, sources)`, `SourceRegistry.read(pointer)`, `SourceRegistry.merge(*pointers)`.
- Schema: `SourceRecord(id: str, url: str, title: str, snippet: str, retrieved_at: datetime, verified: bool)`.
- Verification (optional): `verify_http(timeout=5)` checks each URL's HTTP status, marks `verified=True/False`. Off by default; opt in via `MONET_SOURCE_REGISTRY_VERIFY_HTTP=true` or per-call kwarg. Evaluation showed unverified registries still produce 10/10 citation integrity at the registry-validation level — HTTP verification is an extra layer against redirect/404 churn, not the critical primitive.
- Not a free-standing service. Sits on top of existing `ArtifactService`.

### `src/monet/agents/writer/` — constrained writer

- Writer accepts a `source_registry_pointer` in its context (passed by the execution graph as part of the wave's predecessor artifacts). If absent, writer falls back to current unconstrained behavior with a warning — don't hard-fail existing user graphs.
- Writer prompt constrains citation format: inline `[N](url)` where `N` is a registry ID, url from registry only. No invented URLs.
- Post-generation: `RegistryValidator` scans the output, checks every cited ID against the registry, strips or flags any that do not match. Drop policy configurable (`strip` vs `fail`).
- Output artifact metadata gains a `cited_sources: list[str]` field (registry IDs actually cited).

### Dependency and config

- New optional extra: `gpt-researcher` in `pyproject.toml` under an `[research]` extra.
- New env vars: none from monet side. GPT Researcher reads `OPENAI_API_KEY`, `TAVILY_API_KEY`, `GOOGLE_API_KEY`, etc. directly. Keep monet out of that layer.
- Config: `MONET_RESEARCHER_MODEL` (current) still works — passed through to GPT Researcher's configuration. Add `MONET_RESEARCHER_REPORT_TYPE` for `fast`/`deep` → GPT Researcher report-type mapping.

### Back-compat

- `@researcher` agent id, command names (`fast`, `deep`), input signature, output type (str), artifact write semantics all preserved.
- Users who were depending on the Exa path explicitly: document the removal in the migration PR; offer one release of dual-running with a deprecation warning if any adopters exist at cutover time.

## Phases

1. **Source registry primitive.** Ship `SourceRegistry` as a typed artifact wrapper. No agent changes yet. Ship tests. This is the contract that everything else depends on.
2. **Researcher swap.** Replace Exa/Tavily with GPT Researcher behind the existing `researcher/fast` and `researcher/deep` commands. Researcher writes both the main artifact and a `source_registry` artifact. Main output quality ≥ current quality on the eval suite.
3. **Constrained writer.** Update `writer` to consume the registry when present. Add `RegistryValidator` post-pass. Fallback path preserved for graphs that don't pass a registry pointer.
4. **Hook wiring.** A graph hook (or execution-graph behavior) passes the registry pointer from researcher outputs into writer inputs automatically for the default pipeline. Custom graphs opt in.
5. **Remove fallback paths** in researcher once GPT Researcher path is proven in production. Tavily ReAct deleted. Exa-direct path kept only if the registry pattern has been retrofitted onto it — otherwise deleted. Reduces the agent's surface area.

## Non-goals

- **Replacing the writer with a pydantic-ai or LangGraph-native rewrite.** Out of scope for this migration. The writer's structured output and the writer's constrained citation are separable concerns — the constrained-writer change is the one the evaluation called out as decisive.
- **HTTP verification as a hard requirement.** The evaluation shows registry validation (no HTTP) already hits 10/10 on citation integrity for outputs the agent produced. HTTP verification is a defense-in-depth add-on, not a prerequisite.
- **A universal research-abstraction layer across agents.** Do not generalize `SourceRegistry` into a "provenance" primitive speculatively. Keep it specific to research/writer until a second use case appears.

## Open questions

- **Where does the registry live in the graph state?** Two options: (a) as a separate `ArtifactPointer` passed alongside the main findings pointer through the wave context; (b) nested inside the researcher's output artifact as a linked sub-artifact. Prefer (a) for uniformity with existing pointer-only state.
- **Does the registry survive across waves?** A deep-research question may span multiple research waves. Merging registries across waves is straightforward (`SourceRegistry.merge`) but needs a declared lifecycle: per-run, per-wave, per-node. Per-run is the least surprising.
- **GPT Researcher subclassing boundary.** Evaluation scored GPT Researcher's extensibility 8.0/10 on the back of `PromptFamily` subclassing and 15+ retriever registry. Decide whether monet subclasses to inject prompts/retrievers, or accepts vanilla GPT Researcher behavior. Start with vanilla; extend only if concrete friction emerges.
- **Fast vs. deep mapping.** `researcher/fast` maps to GPT Researcher's quickest report type; `researcher/deep` to the detailed one. Validate that `fast` stays sub-45s on benchmark queries after the swap — one of the current path's strengths is low latency.
- **Writer agent scope.** Does `writer` stay one agent with one constrained-mode, or split into `writer/draft` (registry-aware) and `writer/compose` (free-form)? Defer until the migration is done and usage patterns emerge.

## Trigger-back to roadmap

This migration, when picked up, replaces the current researcher entry in the `## Roadmap` → `### Lower priority / triggered` → "Reference agent quality pass" scope and is the canonical reference example for AgentStream/signal usage after shipping. Close the loop there.
