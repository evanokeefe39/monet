# Examples Restructure: Three-Tier Quickstart

## Intent

Replace the current single quickstart + social_media_llm with three graduated
examples that lower the barrier to first run and provide a clear path from toy
demo to production deployment.

## Plan

### Phase 1: quickstart-local (toy demo, single process, no infra)

Goal: `uv run python run.py "topic"` and see agents work. One file, one terminal.

- [ ] Create `examples/quickstart-local/`
- [ ] `run.py`: single-file entry point using `bootstrap()` + in-process graphs
  - Uses `InMemoryTaskQueue`, `MemorySaver`, filesystem `.catalogue`
  - Prints triage result, planning brief summary, wave progress, artifact previews
  - Display quality matches `client.py` (not the bare JSON dump in current `__main__.py`)
- [ ] `pyproject.toml`: minimal deps — `monet[examples]` + editable source
- [ ] `README.md`: ~20 lines — install, set GEMINI_API_KEY + GROQ_API_KEY, run, see output
- [ ] Consider `--mock` flag: registers stub agents returning canned AgentResults
  so the orchestration flow is visible without any API key

### Phase 2: quickstart-server (real evaluation, client/server, no Docker)

Goal: two terminals, `langgraph dev` server + client. Shows production topology locally.

- [ ] Create `examples/quickstart-server/`
- [ ] Migrate current `examples/quickstart/` content here (server_graphs.py, client.py, langgraph.json)
- [ ] Fix server_graphs.py: replace `_lazy_enqueue` monkey-patch with `bootstrap()`
  - If bootstrap() needs a lazy-worker mode for langgraph dev, add a `lazy_worker=True` param
- [ ] `README.md`: explains why client/server matters (scaling, graph versioning, remote workers)
  and links to quickstart-local for simpler path
- [ ] `pyproject.toml`: `monet[examples]` + `langgraph-sdk` + `langgraph-cli[inmem]`

### Phase 3: deployed (production infra, Docker Compose)

Goal: `docker compose up` and point a client at real infra.

- [ ] Create `examples/deployed/`
- [ ] `docker-compose.yml`: LangGraph server, Postgres (checkpointing + catalogue), Langfuse (tracing)
  - Derive from existing `docker-compose.dev.yml` at repo root
  - Add LangGraph server container (or document running it locally pointed at Postgres)
- [ ] `server_graphs.py`: uses Postgres-backed catalogue + checkpointer via env vars
- [ ] `langgraph.json`: same shape, env pointed at Postgres
- [ ] `.env.example`: all service connection strings + API keys
- [ ] `client.py`: same client as quickstart-server, just pointed at the Docker server
- [ ] `README.md`: architecture diagram (text), explains each service, how to view traces in Langfuse,
  how to swap in real cloud infra (managed Postgres, hosted Langfuse, etc.)

### Phase 4: Cleanup

- [ ] Remove `examples/quickstart/` (replaced by quickstart-server)
- [ ] Remove or archive `examples/social_media_llm/` — its client logic is now in the SDK
  (`monet.client`), its display logic should be in quickstart-local
- [ ] Improve `src/monet/__main__.py` display output (or make quickstart-local the canonical CLI demo)
- [ ] Verify `.gitignore` covers `.catalogue/` in examples (already covered by `.*` rule)

## Open questions (must resolve before execution)

1. Should quickstart-local reuse `__main__.py` or be standalone? Leaning standalone
   since `__main__.py` serves a different purpose (CLI entry point for the installed package).
2. For deployed: do we containerize the LangGraph server ourselves (Dockerfile) or
   use the official `langgraph-cli` Docker image? The CLI has `langgraph build` for this.
3. Should we keep social_media_llm at all? It has custom prompts/display that go beyond
   the SDK's built-in agents. Could become a fourth "custom agents" example later.

## Definition of done

- [ ] Three example directories: quickstart-local, quickstart-server, deployed
- [ ] Each has a README that is self-contained (no cross-referencing required to run)
- [ ] quickstart-local runs with zero infra (single process)
- [ ] quickstart-server runs with zero Docker (langgraph dev)
- [ ] deployed runs with docker compose up
- [ ] Old quickstart/ removed
- [ ] All examples use bootstrap() — no manual init or monkey-patching
