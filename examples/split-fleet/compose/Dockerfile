FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

# -----------------------------
# Builder stage — install monet with its [examples] extra (aegra-cli,
# LLM providers, reference agents). No example pyproject needed inside
# the image; agents/ and graphs/ ship as plain modules under /app.
# -----------------------------
FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Build context = repo root. Install monet editable from its own pyproject.
COPY pyproject.toml ./pyproject.toml
COPY README.md ./README.md
COPY src/ ./src/
RUN uv venv /app/.venv \
    && . /app/.venv/bin/activate \
    && uv pip install --no-cache -e ".[examples]"

# -----------------------------
# Runtime stage — single image drives both server and workers via CMD
# -----------------------------
FROM base AS final

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY examples/split-fleet/agents /app/agents
COPY examples/split-fleet/graphs /app/graphs
COPY examples/split-fleet/monet.toml /app/monet.toml
COPY examples/split-fleet/compose/aegra.json /app/aegra.json
COPY examples/split-fleet/compose/server_graphs.py /app/server_graphs.py

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 2026

USER app

# Default CMD runs the server; worker services override it in compose.
CMD ["aegra", "serve", "--host", "0.0.0.0", "--port", "2026"]
