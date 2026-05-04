# E2E Testing — Real Agent Integration

## Overview

Validate worker composition model end-to-end using real agents from
~/repos/claw-bot-evals. Each agent gets a combined Docker image containing
the real agent process plus a thin adapter that speaks monet's /task protocol.

---

## Prerequisites

### P1: DockerBackend must return a reachable address — DONE

Added `ContainerSpec.expose_port` and `PoolConfig.agent_port`. DockerBackend
publishes the container port to a random host port and returns
`http://localhost:{host_port}` as endpoint address. Workload layer passes
`pool.agent_port` through. 889 tests pass, mypy clean.

### P2: .env with API keys — DONE

Copied from ~/repos/claw-bot-evals/.env. Keys: NVIDIA_NIM_API_KEY,
GROQ_API_KEY, TAVILY_API_KEY, EXA_API_KEY. Both agents route through NIM
with deepseek-v4-pro. Zero Anthropic/OpenAI spend.

---

## Agent selection

| Agent | Transport | Why selected | Image |
|---|---|---|---|
| Pi | HTTP POST /chat, NDJSON streaming | Fast build (2min), /health, streaming | Build from claw-bot-evals/pi-agents |
| ZeroClaw | REST :3002, OpenAI-compatible | Pre-built pull, 93MB, config-driven | ghcr.io/zeroclaw-labs/zeroclaw:latest |

Skipped: NanoClaw (Docker socket), IronClaw (20min build, wizard),
Hermes (8.3GB, wizard), Nanobot (SYS_ADMIN), PicoClaw (web UI only).

---

## Adapter architecture

Each adapter is a single-container Docker image:
1. Starts the real agent as a subprocess or internal service
2. Exposes /task (monet protocol) and /health on port 8080
3. Translates monet payload to agent's native API
4. Returns agent response as JSON object

### Pi adapter

Dockerfile: multi-stage, node:22-slim for Pi + python:3.12-slim for adapter.
Pi runs on internal port 9000. Adapter on 8080.

Protocol translation:
```
POST /task {task_id, payload: {task, context, command, agent_id}}
  → POST http://localhost:9000/chat {message: payload.task, session_id: task_id}
    query: stream=false
  ← {message: "response text"}
  → {output: "response text"}
```

Health: GET /health → proxy Pi GET /health on :9000.

Env vars passed through: NVIDIA_NIM_API_KEY, OPENAI_BASE_URL, OPENAI_API_KEY,
LLM_PROVIDER, LLM_MODEL, TAVILY_API_KEY, EXA_API_KEY.

### ZeroClaw adapter

Dockerfile: multi-stage, zeroclaw official image for binary + python:3.12-slim
for adapter. ZeroClaw on internal port 3002. Adapter on 8080.

Protocol translation:
```
POST /task {task_id, payload: {task, context, command, agent_id}}
  → POST http://localhost:3002/v1/chat/completions
    {model: "deepseek-ai/deepseek-v4-pro", messages: [{role: "user", content: payload.task}]}
  ← {choices: [{message: {content: "response"}}]}
  → {output: "response"}
```

Health: GET /health → TCP probe on :3002 (ZeroClaw has no /health endpoint).

Requires config.toml mounted into the image at build time.

### Pi-gateway adapter (for T5)

Same as Pi adapter + writes result as artifact to gateway before returning.
Reads MONET_GATEWAY_URL and MONET_TOKEN from env.

```python
# After getting Pi response:
httpx.post(f"{gw_url}/artifacts/{task_id}/research_output",
           headers={"Authorization": f"Bearer {token}"},
           content=response_text)
```

---

## Test scenarios

### T1: Docker managed + Pi (core lifecycle)

```toml
[pools.pi-managed]
backend = "docker"
workload = "task"
image = "monet-e2e/pi-agent:latest"
agent_port = 8080
concurrency = 2
task_timeout_s = 120
startup_timeout_s = 45
graceful_shutdown_s = 10
```

Flow: enqueue → claim → DockerBackend.start(expose_port=8080) →
_wait_ready polls /health → HTTPSession POST /task → adapter → Pi /chat →
LLM via NIM → response → session.receive() → DockerBackend.stop() → done.

Validates: managed lifecycle, Docker port publishing, HTTP transport,
real LLM round-trip, startup/shutdown.

### T2: Docker persistent + Pi (warm pool reuse)

```toml
[pools.pi-persistent]
backend = "docker"
workload = "persistent"
image = "monet-e2e/pi-agent:latest"
agent_port = 8080
warm_pool_size = 2
concurrency = 2
task_timeout_s = 120
startup_timeout_s = 45
heartbeat_interval_s = 10
restart_policy = "on_failure"
max_restarts = 3
```

Flow: supervisor starts 2 containers → TaskRouter registers idle →
enqueue 3 tasks → first 2 acquire one each → third blocks until release →
verify only 2 containers started.

Validates: warm pool, TaskRouter acquire/release, container reuse,
back-pressure.

### T3: Docker managed + ZeroClaw

```toml
[pools.zeroclaw-managed]
backend = "docker"
workload = "task"
image = "monet-e2e/zeroclaw-agent:latest"
agent_port = 8080
task_timeout_s = 120
startup_timeout_s = 60
```

Same lifecycle as T1 with ZeroClaw. Longer startup (model config load).

Validates: managed lifecycle with pre-built third-party agent, different
LLM provider path.

### T4: Mixed-pool single worker

```toml
[pools.local]
backend = "in_process"

[pools.pi-pool]
backend = "docker"
workload = "task"
image = "monet-e2e/pi-agent:latest"
agent_port = 8080
task_timeout_s = 120

[pools.zeroclaw-pool]
backend = "docker"
workload = "task"
image = "monet-e2e/zeroclaw-agent:latest"
agent_port = 8080
task_timeout_s = 120
```

One @agent in-process + Pi + ZeroClaw. One worker, three pools, three tasks.

Validates: multi-pool claim loop, routing by backend, concurrent execution.

### T5: Gateway data plane round-trip

```toml
[pools.pi-gateway]
backend = "docker"
workload = "task"
image = "monet-e2e/pi-gateway-agent:latest"
agent_port = 8080
task_timeout_s = 120

[gateway]
port = 2027
```

Pi-gateway adapter writes artifact to gateway after LLM response. Worker
verifies artifact exists post-completion.

Validates: embedded gateway, JWT minting, artifact write from container,
artifact read from worker, task-scoped isolation.

### T6: Failure modes

All use Pi adapter.

- **T6a timeout**: task_timeout_s = 5, slow prompt → cancel → cleanup
- **T6b crash**: kill container mid-task → TransportError → fail → no orphan
- **T6c startup timeout**: startup_timeout_s = 1 → _wait_ready fails → stop
- **T6d agent error**: malformed payload → HTTP 400 → AgentError → fail

---

## File layout

```
examples/agent-adapters/
    README.md
    pi/
        Dockerfile
        adapter.py
        requirements.txt
    zeroclaw/
        Dockerfile
        adapter.py
        config.toml
        requirements.txt
    pi-gateway/
        Dockerfile
        adapter.py
        requirements.txt
tests/e2e/
    conftest.py              # add Docker image build fixtures
    test_e2e_pi_managed.py
    test_e2e_pi_persistent.py
    test_e2e_zeroclaw_managed.py
    test_e2e_mixed_pool.py
    test_e2e_gateway_roundtrip.py
    test_e2e_failure_modes.py
    fixtures/
        monet-e2e.toml
```

---

## Build order

- [x] P1: DockerBackend addressing
- [x] P2: .env with API keys
- [x] Pi adapter image (examples/agent-adapters/pi/)
- [x] T1: test_e2e_pi_managed.py
- [x] ZeroClaw adapter image (examples/agent-adapters/zeroclaw/)
- [x] T3: test_e2e_zeroclaw_managed.py
- [x] T4: test_e2e_mixed_pool.py
- [x] T2: test_e2e_pi_persistent.py
- [x] Pi-gateway adapter + T5: test_e2e_gateway_roundtrip.py
- [x] T6: test_e2e_failure_modes.py

---

## Dependency graph

```
P1 (done) ──┬── Pi adapter
             │    ├── T1 (pi managed)
             │    ├── T2 (pi persistent)
             │    ├── T4 (mixed pool) ← also needs ZeroClaw adapter
             │    ├── T6 (failure modes)
             │    └── Pi-gateway adapter
             │         └── T5 (gateway roundtrip)
             └── ZeroClaw adapter
                  ├── T3 (zeroclaw managed)
                  └── T4 (mixed pool)
```
