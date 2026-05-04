# Use zeroclaw official image as base; add python3 for the adapter.
# The adapter starts zeroclaw as a subprocess and exposes monet /task on 8080.
FROM ghcr.io/zeroclaw-labs/zeroclaw:latest

# Install python3 — zeroclaw's base image is Debian-based.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

COPY config.toml /etc/zeroclaw/config.toml

WORKDIR /app
COPY adapter.py ./
EXPOSE 8080
ENTRYPOINT []
CMD ["python3", "adapter.py"]
