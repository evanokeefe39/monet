# Examples Restructure: Three-Tier Quickstart

## Status: Complete

Restructured examples/ into three graduated examples:

- [x] `examples/quickstart/` — zero infrastructure, `monet dev` + `monet run`
- [x] `examples/local/` — Docker Compose with Postgres + optional Langfuse (profiles)
- [x] `examples/deployed/` — Railway with managed infrastructure (Neon, Upstash, Langfuse Cloud)

Removed:
- [x] `examples/social_media_llm/` — deprecated, superseded by SDK client
- [x] `examples/quickstart-local/` — redundant, absorbed into quickstart README
- [x] `tests/test_example_cli.py` — tested deleted social_media_llm code
