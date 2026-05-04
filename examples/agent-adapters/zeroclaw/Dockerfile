# Multi-stage: extract zeroclaw binary (distroless) into python:3.12-slim.
FROM ghcr.io/zeroclaw-labs/zeroclaw:latest AS zeroclaw

FROM python:3.12-slim
COPY --from=zeroclaw /usr/local/bin/zeroclaw /usr/local/bin/zeroclaw

COPY config.toml /etc/zeroclaw/config.toml

WORKDIR /app
COPY adapter.py ./
EXPOSE 8080
ENTRYPOINT []
CMD ["python3", "adapter.py"]
