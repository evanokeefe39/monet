# Stage 1: install Pi node dependencies
FROM node:22-slim AS pi-install
WORKDIR /pi
COPY pi-src/package.json ./
RUN npm install
COPY pi-src/server.ts ./

# Stage 2: final image — node runtime + python3 for the adapter
FROM node:22-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=pi-install /pi /pi
WORKDIR /app
COPY adapter.py ./
EXPOSE 8080
CMD ["python3", "adapter.py"]
