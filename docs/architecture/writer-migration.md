# Writer Migration — Section-Level Editing with Context Engineering

**Status:** proposed migration, not implemented. Design informed by ongoing independent research.
**Trigger to execute:** first concrete user request to produce or revise a long-form document (report, spec, article, multi-chapter output) where today's single-shot writer either (a) exceeds the context window, (b) degrades in coherence past ~8–10K tokens of output, or (c) forces the user to re-run the whole document to change one section.
**Relation:** complements `docs/architecture/researcher-migration.md` (source registry) and `docs/architecture/planner-structured-output.md` (validated work brief). The three migrations together form the default-pipeline quality pass.

## Research in flight

Active investigation of what makes a good writer agent is being run in a separate repository: **`~/repos/agent-writer`**. Same playbook the research-agent evaluation used (landscape → rubric → shortlist → smoke test → benchmark → decision), now applied to writer-category agents. Current state: `EVALUATION-PROCESS.md` captures the reusable methodology; `eval/queries_template.json`, `eval/rubrics.md`, `eval/harness/`, and `eval/scorecard/` are scaffolded; benchmark runs and final report not yet produced. **This migration spec describes the architectural target (section-level editing, composite-document model, context budgets) independent of which specific writer framework or model wins the evaluation** — the model/framework choice slots in under `draft_section` / `edit_section` implementation without changing the surrounding contract. When that evaluation concludes, its `EVALUATION-REPORT.md` becomes the authority on the decision analogous to how the research-agent report drove `docs/architecture/researcher-migration.md`.

## Problem

`src/monet/agents/writer/__init__.py` today has a single command `writer/deep` that:

- Loads the full `task` + full `context` (resolved via `resolve_context`) into one LLM call.
- Produces a single string, writes it as one artifact with `content_type="text/plain"`, confidence 0.8.
- Has no section addressing, no incremental edits, no way to modify one part without regenerating everything.

Three failure modes result:

1. **Context bloat on long outputs.** A 15K-word report requires ~20K output tokens plus whatever research context was passed in. Input context grows with document length — research findings, prior sections, style guides. A single-shot approach hits provider limits before finishing.
2. **Coherence degradation.** Even within context limits, output quality drops past ~8–10K tokens due to attention dilution ("lost in the middle"). A model that writes a tight opening often loses the thread by the conclusion.
3. **Revision waste.** To change one paragraph, the user re-runs the whole `writer/deep` call with updated instructions. Every revision rebuilds every section from scratch — token cost, latency, and consistency loss (a regenerated section 2 may now contradict section 4, which wasn't regenerated).

This is a **context engineering** problem, not a prompting problem. The answer is to change what goes into the context window per call, not to write better prompts for the same oversized call. This lines up with the stated architectural priority (`CLAUDE.md` → `## Architecture` → "context engineering is prioritized over prompt gymnastics").

## Target state

### Composite document model

A "document" is a manifest artifact plus N section artifacts, not a single blob.

```
artifact: document:<id>/manifest
  {
    "title": "...",
    "outline": [
      {"id": "s-intro", "title": "Introduction", "order": 1,
       "goal": "...", "content_pointer": "...", "summary_pointer": "...",
       "cited_sources": ["r-3", "r-7"], "status": "draft|edited|clean"},
      ...
    ],
    "version": 3
  }

artifact: document:<id>/section/s-intro    ← editable unit
artifact: document:<id>/section/s-intro/summary   ← ≤400 chars, regenerated on edit
...
```

Sections are the editable unit. The manifest is the index. Summaries are the cross-section context carrier (neighbors never load each other's full content).

### Writer commands

Replace `writer/deep` with a command set. Each command has a declared context profile — what it loads, what it writes.

| Command | Input context | Output | Confidence |
|---|---|---|---|
| `outline` | `brief` + source-registry summary | `OutlinePlan` (list of `SectionPlan`) — JSON | 1.0 |
| `draft_section` | `section_id`, `section_plan`, `neighbor_summaries`, `relevant_sources` (registry subset) | section content (markdown) | 0.8 |
| `edit_section` | `section_id`, `instruction`, `current_content`, `neighbor_summaries`, `relevant_sources` | replacement content or unified diff | 0.7 |
| `compose` | `document_id` | final stitched document (markdown) | 1.0 |
| `review_document` | `document_id`, `brief` | coherence report + per-section findings | 0.7 |

The legacy `writer/deep` stays as a thin alias: `outline → draft_section` in parallel for small documents → `compose`. Keeps the one-shot UX for users who don't need section control.

### Context profile per command

The budget and content loaded per call are explicit, not implicit in the prompt. `draft_section` and `edit_section` are the two calls whose context must be tightly constrained; that's where the savings come from.

- `draft_section` loads: the current section's plan (≤1K tokens), neighbor summaries only (N×≤400 chars), the subset of the source registry tagged for this section (declared at outline time), document-level style guide if any. It **does not** load other sections' full content. Target: ≤3K input tokens per call regardless of document length.
- `edit_section` loads: the current section's current content, the edit instruction, the same neighbor summaries, the same source subset. It does not re-load other sections.
- `compose` does not call the LLM — it concatenates section artifacts in outline order with heading normalization. Pure assembly.
- `review_document` is the only step that walks all sections. It loads summaries + outline + a short sample from each section, not full content.

### Cross-section coherence

Edits invalidate assumptions. Handle at the signal layer, not by always regenerating.

- When `edit_section` changes content that contradicts a declared claim in another section (e.g., renames a concept, reverses a claim), the writer emits an `AUDIT` signal naming the affected section IDs. Orchestrator surfaces these; human or `review_document` decides whether to re-draft the affected sections.
- Each section artifact carries a `status: "clean" | "dirty"` flag in the manifest. Edits mark neighbors `dirty` only when the edit's signals say so. A graph hook or post-edit slot can auto-invoke `edit_section` on `dirty` neighbors with a "reconcile with: <diff>" instruction.
- `review_document` is the formal coherence pass. Catches contradictions edits missed.

### Source registry integration

Inherits from `researcher-migration.md`. Outline assigns a subset of the source registry to each section (`cited_sources: list[str]` per `SectionPlan`). `draft_section` and `edit_section` receive only that subset. The constrained-writer contract (citations must resolve to the registry) holds per-section — post-validation strips fabricated citations at the section artifact level, before manifest update.

### Planner integration

If `planner-structured-output.md` ships structured planner output, `WorkBrief` gains an optional `outline: list[SectionPlan]` field. The planner produces the outline when it can; otherwise the writer's `outline` command fills in. Either path produces the same `OutlinePlan` artifact shape — the planner and writer agents are interchangeable at the outline layer.

## Phases

Ship order, each usable without the next.

1. **Composite-document primitive.** Ship `DocumentManifest` pydantic model + typed artifact wrapper. No writer changes yet. Tests cover: add section, update section, list sections, render manifest. This is the contract.
2. **`outline` + `draft_section` + `compose`.** MVP section-based writer. Flow: `outline` → N parallel `draft_section` calls (wave-style dispatch) → `compose`. Legacy `writer/deep` still works. This unlocks long-form outputs that exceed single-call budgets.
3. **`edit_section`.** Incremental editing. Decide diff vs. replacement policy before shipping (open question below).
4. **Cross-section signals + `dirty` flag.** Edits surface their radius. Auto-reconcile behavior is opt-in via a graph hook or pipeline slot, not default.
5. **`review_document`.** Coherence pass. Optional step in pipelines that need it.
6. **Retire `writer/deep`** (or leave as alias). Decide based on whether users still call it directly for small docs.

## Non-goals

- **Git-like document versioning.** Section artifacts are overwritten on edit; the artifact store's own history (if present) is the audit trail. No branch/merge/tag primitives.
- **Rich formatting (HTML/LaTeX/DOCX).** Stay on markdown. User code can render to any format downstream.
- **Multi-author concurrency.** One writer at a time per document. Locking, CRDT, three-way merge — all out of scope.
- **Reusable document-primitive for non-writer agents.** Do not generalize `DocumentManifest` into a generic "composite artifact" until a second use case appears. Resist overproduction.
- **Dependency on a specific LLM editor ability.** The design is model-agnostic. Replacement-style edits work on any model; diff-style requires decent patch-generation ability and is gated by the open question below.

## Open questions

- **Diff vs. replacement for `edit_section`.** Replacement is safer (model emits full new section, no patch-applier needed) but wastes output tokens on unchanged prose. Diff is cheaper but models produce malformed diffs ~5–15% of the time across providers. Decision: start with **replacement**; add diff as a second-pass optimization once we have telemetry on section sizes and edit frequency. The source-registry constrained-writer contract is simpler over full replacements too.
- **Stable section IDs vs. heading-derived IDs.** Stable IDs (UUIDs or slugs assigned at outline time) survive heading rewrites; heading-derived IDs break when titles change. Decision: stable IDs, generated at outline, carried in manifest. Headings are content, not addresses.
- **Who owns the outline: planner, writer, or separate `outliner` agent?** Proposal: both planner and writer can produce an `OutlinePlan`. Planner produces it when the `WorkBrief` includes long-form output. Writer produces it when the brief does not. Same output type. No new agent.
- **Neighbor summary generation.** Summary is regenerated on every section edit? Or only when the section's diff exceeds a threshold? Decision: regenerate on every edit initially, because summaries are short and the cost is small; revisit if profiling shows otherwise.
- **Compose rendering of citations.** If two sections cite the same source, does the composed document have one footnote or two? Needs a post-compose citation-normalizer that deduplicates. Lives in `compose`, not in section writers.
- **Section-level wave dispatch.** The obvious implementation is "N sections → N parallel `draft_section` waves". But the wave infrastructure is currently routed by agent, not by section. Clarify whether `draft_section` appears N times in the routing skeleton with distinct `node_id`s, or whether the writer internally fans out. Prefer the former — it reuses the existing execution-graph primitives.
- **Token budget enforcement.** Soft budget (emit `AUDIT` signal on exceed) or hard budget (`EscalationRequired` on exceed)? Start soft.

## Expected impact

Not a guarantee — these are the order-of-magnitude claims the design is reaching for. Track them after phase 2 lands.

- **Edit cost scales with section size, not document size.** Editing one 500-word section in a 20K-word document costs ~1K output tokens instead of 20K. An order of magnitude per edit; a larger order as document grows.
- **Coherence at length.** Each `draft_section` call produces ≤2K output tokens — well inside the high-quality output regime for modern models. Aggregate document quality becomes a function of outline quality + per-section quality + coherence pass, all of which are narrowable problems.
- **Revision latency.** Most revisions touch one section. Expected median revision time drops from "full document regeneration" to "one section call + summary regen".
- **Context window headroom.** Input context per call stays bounded regardless of document length. No context-window ceiling on document length as a result of the writer's own design; other constraints (compose output size, artifact store) dominate.

## Relationship to other deferred work

- **`docs/architecture/researcher-migration.md`** — source registry is the citation contract between research and writing; section-level scoping of the registry is declared at outline time. Write this migration to expect the registry; ship a fallback for when it's absent.
- **`docs/architecture/planner-structured-output.md`** — `WorkBrief` optionally carries an outline. Independent, but the two compose.
- **`graph-extension-points.md`** — the `post_plan_review` slot is a natural place to approve/revise an outline. The `after_wave` slot is a natural place to enforce section-level review gates. Neither is required for this migration to land.
- **`memory-service.md`** — an agent memory of "style decisions, rejected phrasings, terminology" across a document's lifetime is a plausible consumer of a memory primitive once it exists. Not a prerequisite.
- **`CLAUDE.md` → `### Lower priority / triggered` → "Reference agent quality pass"** — this is the third of the three scoped migrations under that umbrella (researcher, planner, writer). When all three land, the reference-agent quality bar is materially raised.
