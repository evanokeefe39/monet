FROM python:3.12-slim

WORKDIR /app

# Install monet with adapter dependencies
RUN pip install --no-cache-dir "monet[adapter]" 2>/dev/null || \
    pip install --no-cache-dir fastapi uvicorn httpx pydantic

# Copy the package source for development installs
# In production builds, replace this with: pip install monet==<version>
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e . --no-deps

EXPOSE 8080

# Mount your config at /etc/monet/adapter.toml or pass CONFIG_PATH
ENV CONFIG_PATH=/etc/monet/adapter.toml

CMD ["sh", "-c", "monet adapter serve ${CONFIG_PATH}"]
