# Model Catalog

Free models prioritized: NVIDIA NIM → Groq → Gemini. Within each provider, sorted newest first.
Swap order when rate limits are hit during testing.

NIM base URL: `https://integrate.api.nvidia.com/v1`  
Groq base URL: `https://api.groq.com/openai/v1`  
Gemini base URL: `https://generativelanguage.googleapis.com/v1beta/openai/`

---

## Opus tier

| Priority | Model ID | Provider | Released | Free | Notes |
|---|---|---|---|---|---|
| 1 | `deepseek-ai/deepseek-v4-pro` | NIM | Apr 24 2026 | free endpoint | MoE, 1M ctx, coding flagship |
| 2 | `nvidia/nemotron-3-super-120b-a12b` | NIM | — | free endpoint | 120B MoE Mamba-Transformer, 1M ctx |
| 3 | `openai/gpt-oss-120b` | Groq | Mar 2026 | free, 1k RPD | 120B open-weight, 131K ctx, ~500 tps |
| — | `moonshotai/kimi-k2.6` | NIM | Apr 13 2026 | **paid** ($1/GPU·hr) | Skip for free eval; use K2.5 instead |

Gemini has no free Opus-tier model (Pro and 3.1 Pro are paid-only).

---

## Sonnet tier

| Priority | Model ID | Provider | Released | Free | Notes |
|---|---|---|---|---|---|
| 1 | `moonshotai/kimi-k2.5` | NIM | Jan 27 2026 | free | 1T MoE, 256K ctx, multimodal |
| 2 | `deepseek-ai/deepseek-v4-flash` | NIM | Apr 2026 | free endpoint | 284B MoE, 1M ctx, speed-optimised |
| 3 | `moonshotai/kimi-k2-instruct-0905` | NIM | Sep 2025 | free | 32B active, 256K ctx |
| 4 | `qwen/qwen3.5-122b-a10b` | NIM | — | free endpoint | 10B active, tool calling |
| 5 | `meta-llama/llama-4-scout-17b-16e-instruct` | Groq | Apr 2026 | free, preview | 131K ctx, 750 tps, multimodal |
| 6 | `qwen/qwen3-32b` | Groq | — | free, 60 RPM | 131K ctx, 400 tps |
| 7 | `llama-3.3-70b-versatile` | Groq | — | free, 1k RPD | 131K ctx, 280 tps, production stable |
| 8 | `gemini-3-flash-preview` | Gemini | — | free, preview | frontier-class per Google docs |
| 9 | `gemini-2.5-flash` | Gemini | — | free, stable | 1M ctx, best free Gemini |

---

## Haiku tier

| Priority | Model ID | Provider | Released | Free | Notes |
|---|---|---|---|---|---|
| 1 | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | NIM | — | free endpoint | 30B MoE, omni-modal (text/image/video/speech) |
| 2 | `meta/llama-4-scout-17b-16e-instruct` | NIM | Apr 2026 | free | multimodal, 17B active |
| 3 | `openai/gpt-oss-20b` | Groq | Mar 2026 | free, 1k RPD | 1k tps — fastest model on platform |
| 4 | `llama-3.1-8b-instant` | Groq | — | free, 14.4k RPD | 131K ctx — highest free quota surveyed |
| 5 | `gemini-3.1-flash-lite-preview` | Gemini | — | free, preview | budget frontier variant |
| 6 | `gemini-2.5-flash-lite` | Gemini | — | free, stable | lowest-latency Gemini |

---

## Agent wiring

Each agent is wired to the top free Opus-tier model: `deepseek-ai/deepseek-v4-pro` on NIM.

To step down the priority list during testing, update `OPENAI_BASE_URL` and `OPENAI_API_KEY` in `.env`
to point at the next provider, then update the model ID in each agent's config.

| Agent | Wired model | Provider | Config location |
|---|---|---|---|
| Pi | `deepseek-ai/deepseek-v4-pro` | NIM | `pi-agents/.env.example` → `LLM_MODEL` |
| Nanobot | `deepseek-ai/deepseek-v4-pro` | NIM | select OpenAI provider in Nanobot config UI |
| PicoClaw | `deepseek-ai/deepseek-v4-pro` | NIM | select model in PicoClaw web UI |
| IronClaw | `deepseek-ai/deepseek-v4-pro` | NIM | `LLM_MODEL` env var |
| ZeroClaw | `deepseek-ai/deepseek-v4-pro` | NIM | `zeroclaw/config.toml` `[llm]` section |
| Hermes | `deepseek-ai/deepseek-v4-pro` | NIM | set model in Hermes setup wizard |

---

## Provider fallback chain

```
NVIDIA NIM  →  OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1   OPENAI_API_KEY=<nim_key>
Groq        →  OPENAI_BASE_URL=https://api.groq.com/openai/v1        OPENAI_API_KEY=<groq_key>
Gemini      →  OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/  OPENAI_API_KEY=<gemini_key>
```

ZeroClaw uses `config.toml` instead of env — update `[llm]` and `[llm.openai]` directly.

---

## Deprecation notes (April 2026)

- `moonshotai/kimi-k2-instruct` — removed from Groq Oct 10 2025; on NIM only
- `moonshotai/kimi-k2-instruct-0905` — removed from Groq Apr 15 2026; on NIM only
- `moonshotai/kimi-k2-instruct` (Groq) → replaced by `openai/gpt-oss-120b`
- `meta-llama/llama-4-maverick` (Groq) → deprecated Mar 9 2026; Scout is surviving Llama 4
- `deepseek-r1-distill-llama-70b` (Groq) → deprecated Oct 2 2025; no DeepSeek on Groq
- No DeepSeek V5, Qwen 4, Kimi K3, or Llama 5 released as of Apr 30 2026
