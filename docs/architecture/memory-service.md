# Memory Service (design)

Status: design spec. Not yet implemented. Trigger: concrete user request for cross-run agent memory. See `architecture/roadmap.md`.

## Intent

Make long-lived agent memories first-class in the same way artifacts are. Every agent can write memories; every agent can read relevant memories injected into its context. Memories are agent- and system-facing; artifacts are user-facing.

## Relation to artifact store

Memories and artifacts share surface-level shape (pointer-addressed, pluggable storage backend) but diverge on the axes that matter:

| Axis | Artifact | Memory |
| --- | --- | --- |
| Lifecycle | Write-once, immutable | Mutable or append-only (open question) |
| Retrieval | Pointer lookup by key | Match by scope + relevance (tag, semantic, recency) |
| TTL / eviction | Run- or tenant-bound, retained | Per-memory TTL, LRU cap, compaction candidates |
| Primary reader | Human user, downstream run | Other agents in same scope, same agent on later run |
| Schema | Opaque bytes + metadata | Structured (text + embedding + provenance + tags) |
| Index | Key index | Key index + embedding index + tag index |

Tagging artifacts with `kind="memory"` is rejected: it cannot carry the divergent index, TTL, and retrieval-scoring requirements. Memories are a separate service that may share a storage backend with artifacts but owns its own index.

## Proposed shape (mirrors `src/monet/artifacts/`)

```
src/monet/memory/
  protocol.py         # MemoryClient protocol
  metadata.py         # MemoryMetadata model (scope, tags, embedding, provenance, ttl)
  storage.py          # FilesystemStorage (reuses artifact storage backend if configured)
  index.py            # SQLiteIndex with embedding column + tag table
  memory.py           # InMemoryMemoryClient test double
  service.py          # MemoryService composer
  __init__.py         # memory_from_env()
```

Worker-side ambient stub (peer of `write_artifact`):

```python
from monet import write_memory

write_memory(
    content="user prefers concise responses",
    scope={"tenant_id": ..., "agent_id": "writer"},
    tags=["preference", "style"],
    ttl=None,
)
```

Worker-side injection hook (peer of `hooks/plan_context.py:inject_plan_context`):

```python
# src/monet/hooks/memory.py
@on_hook("before_agent")
def inject_memory(envelope: TaskEnvelope, ctx: AgentRunContext) -> TaskEnvelope:
    memories = memory_service.query(
        scope=derive_scope(ctx),
        query=envelope.task.content,
        limit=MEMORY_BUDGET,
    )
    envelope.task.content = splice_memories(envelope.task.content, memories)
    return envelope
```

Orchestrator state stores memory pointers only — same discipline as artifacts (`feedback_orchestrator_pointer_only`).

## Integration channels

Artifacts are first-class with multiple integration paths: direct SDK methods, optional MCP tool exposure, CLI. Memories inherit the same pattern:

- **SDK**: `write_memory()` ambient stub; `memory_from_env()` for explicit service access; `resolve_memory(pointer)` for pull retrieval in agent code.
- **Hook**: `inject_memory` before-agent hook pushes matched memories into task context without agent action.
- **Tool**: expose `query_memory` / `write_memory` as tools so tool-calling agents can explicitly read or write.
- **MCP**: MCP server exposing the same operations to external clients (parity with the artifact MCP surface if/when it ships).
- **CLI**: `monet memory list|get|put|forget` for operator inspection and manual forget.

Push (hook) and pull (tool) are complementary, not alternatives — see open question #1.

## Open questions

Each must be answered before implementation begins. Resolution may shift the service shape above.

1. **Retrieval surface — push, pull, or hybrid.** Hook-inject top-N by match (push) is opaque to the agent and adds latency to every invocation. Tool-call query (pull) gives agent control but requires tool support and prompt guidance. Hybrid ships both. Decision shapes every other question — pick this first.

2. **Write semantics.** Immutable like artifacts? Append-only per-key? Last-write-wins? Versioned with history? Append-only is the safe default — preserves provenance, compaction handles growth.

3. **Scope key shape.** Candidates: `agent_id`, `tenant_id`, `run_id`, `user_id` / session, global. Likely a multi-dimensional tuple. Every query filters by scope before ranking. Must decide which dims are first-class columns vs. opaque tag string.

4. **Semantic categories.** First-class episodic / semantic / procedural distinction, or user-defined tags only? Tagging wins unless we need category-specific retrieval behaviour.

5. **Cross-agent visibility.** Default: agent B reads agent A's memories in same tenant? Or require explicit `publish=True` on write? Conservative default is private-to-writer, explicit opt-in for cross-agent.

6. **Eviction and TTL.** Options: infinite default, per-memory TTL, LRU cap per scope, manual `forget()`, automatic compaction (merge similar). Unbounded growth = unbounded retrieval cost. Need at least LRU cap and per-memory TTL.

7. **Promotion path.** Some artifacts become memories after the run ends (e.g. a validated user preference). Explicit `promote(artifact_pointer) -> memory_pointer`, or require re-write? Explicit `promote` preserves provenance linkage.

8. **Provenance.** Each memory carries `(run_id, agent_id, timestamp, trace_id, source_artifact_id?)`. Required for audit, required for self-learning agents.

9. **Replay determinism.** A re-run of a prior run: sees frozen memory state from original-run-time, or live current memory? Freezing enables reproducibility; live enables self-correction. Likely: freeze by default, `use_live_memory=True` opt-in.

10. **User-facing CRUD.** Can end users list / edit / delete memories about themselves? GDPR right-to-forget requires at least `forget_by_scope(user_id)`. List / edit is product territory — defer to downstream SaaS.

11. **Sensitivity tier.** Reuse existing `is_sensitive` handling and redaction hooks? Memories with PII need the same guardrails as sensitive artifacts — the infrastructure already exists, confirm it applies.

12. **Tenant boundary.** Once Priority 1 lands: strict tenant scope on every query? Allow tenant-agnostic system memories (SDK-owned, global)? Strict by default, system memories as separate namespace.

13. **Retrieval ranking.** Semantic similarity alone, or weighted mix (similarity × recency × match-count)? Re-rank with LLM? Start with similarity + recency linear mix; upgrade when a concrete failure mode appears.

14. **Budget.** Retrieval tokens per invocation — hard cap, or hook-configurable per agent? Without cap, context inflates silently. Default cap per invocation, override per-agent.

15. **Storage backend sharing.** Memory service shares artifact `FilesystemStorage` backend, or ships independent? Sharing simplifies ops (one bucket, one credential); separating simplifies tenant data deletion. Decision probably: share backend, separate index DB.

## Implementation order (when triggered)

1. Resolve open questions #1 (retrieval surface) and #3 (scope key shape) with the user.
2. Lift `src/monet/artifacts/` layout into `src/monet/memory/`, diverge metadata + index.
3. Add `write_memory()` ambient stub and `memory_from_env()`.
4. Ship `inject_memory` hook if push retrieval wins, or `query_memory` tool if pull wins, or both.
5. Add `MonetClient.memories` query surface for host applications.
6. Add `monet memory` CLI group.
7. MCP server exposure (parity with artifact MCP if/when that ships).
8. Self-learning example under `examples/` that writes memories on one run and reads them on the next.

## Negative space

- Memory service is not a vector database product. It uses an embedding index; it is not a competitor to Pinecone or pgvector — those remain pluggable storage backend choices.
- Memory service does not own retrieval policy for user-facing RAG features. Those live in user agent code; memory is the agent-and-system-facing long-lived state primitive.
- No user model, no account model, no billing hooks. Those live in the downstream SaaS repo per `feedback_saas_separate_repo`.
