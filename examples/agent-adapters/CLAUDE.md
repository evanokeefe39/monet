# Agent Adapters

## Model selection

All adapters in this directory are wired to free LLM endpoints. The canonical model
catalog is at `models.md` (repo root) and `docs/reference/models.md`.

Priority order: NVIDIA NIM → Groq → Gemini. Within each provider, newest first.

**Default for all adapters:** `deepseek-ai/deepseek-v4-pro` on NIM.

| Adapter | Config file | Key to change |
|---|---|---|
| `zeroclaw/` | `zeroclaw/config.toml` | `[llm]` section — `model` and `default_provider` |
| `pi/` | `pi-agents/.env.example` | `LLM_MODEL` + `OPENAI_BASE_URL` + `OPENAI_API_KEY` |
| `pi-gateway/` | same as `pi/` | same |

When NIM rate-limits, swap to the next provider by updating `OPENAI_BASE_URL` and
`OPENAI_API_KEY` to Groq credentials and picking the next model in the Sonnet/Opus
tier list from `docs/reference/models.md`.

ZeroClaw does not use env vars for provider config — edit `config.toml` directly.
Key constraint: `nvapi-` keys require `default_provider = "nvidia"` in `[llm]`.

## Groq TPM limits

Groq free tier caps at 12,000 TPM. ZeroClaw system prompt alone is ~19K tokens —
always use NIM for ZeroClaw. Pi system prompt is ~32K tokens — same restriction.

## Adding a new adapter

1. Create a directory under `examples/agent-adapters/<name>/`.
2. Implement `/health` and `/task` per `docs/guides/adapter-protocol.md`.
3. Wire the model via env or config using a provider from `docs/reference/models.md`.
4. Add a `Dockerfile` that builds the adapter image.
5. Document the adapter's protocol quirks in a `README.md` in its directory.
