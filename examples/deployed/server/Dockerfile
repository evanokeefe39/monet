FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

# -----------------------------
# Builder stage
# -----------------------------
FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Build context is the repo root (see docker-compose.yml). Install the
# server example's deps, which pulls in monet via path source.
COPY examples/deployed/server/pyproject.toml ./pyproject.toml
COPY pyproject.toml ./monet-pyproject.toml
COPY src/ ./src/
RUN uv venv /app/.venv \
    && . /app/.venv/bin/activate \
    && uv pip install --no-cache -e . \
    && uv pip install --no-cache aegra-cli

# -----------------------------
# Runtime stage
# -----------------------------
FROM base AS final

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY examples/deployed/server/aegra.json ./aegra.json
COPY examples/deployed/server/server_graphs.py ./server_graphs.py

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 2026

USER app

CMD ["aegra", "serve", "--host", "0.0.0.0", "--port", "2026"]
