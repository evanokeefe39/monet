# ADR-008 — Data plane gateway for cross-network service access

Status: **accepted**
Date: 2026-05-04

## Context

When monet components span multiple networks — a worker on a laptop, burst
jobs on CloudRun, shared services in a VPC — every shared service faces the
same connectivity problem. Artifacts, progress, OTel traces, and signals all
need to be written by agents that may not share a network with the backend
stores.

During design review of the worker composition plan, we traced this through
several manifestations:

1. CloudRun agent needs to write artifacts — but artifact store is S3 in a
   different account, or local filesystem on a laptop
2. CloudRun agent needs to emit progress — but progress store is Postgres in
   a private VPC
3. CloudRun agent needs to export OTel spans — similar reachability question
4. MCP sidecar tools need to POST somewhere — where?
5. Backend credentials (AWS keys, Postgres DSNs) would need to be distributed
   to every agent in every network — credential sprawl

We also identified that having two code paths (worker localhost endpoints for
co-located agents, gateway for remote agents) creates an implicit mode switch
when deployment topology changes. Adding a cloud-push pool to a previously
local-only setup would change how existing agents should behave.

## Research findings

**Industry pattern for cross-network service access:** Cloud-managed services
with authenticated public endpoints (S3, managed Postgres, cloud OTel
collectors) are the common denominator. Prefect uses "blocks" — typed,
named infrastructure abstractions. Dagster uses "resources." All treat each
service dependency as a named, independently configured endpoint.

**Credential isolation:** Enterprise security requires backend credentials
to live in one controlled location, not scattered across agent containers in
multiple networks. API gateways and service proxies are the standard solution.

**Prefect split-plane:** Results go to user-configured storage. Control plane
stores metadata pointers only. Workers are outbound-only. This matches monet's
existing artifact pointer model.

## Decision

### D1: Always-gateway architecture

All agents — local subprocess, Docker, CloudRun, ECS — communicate with
monet's shared services exclusively through the **data plane gateway**. There
is no separate "worker localhost endpoints" path.

The gateway is a stateless HTTP service with a stable API contract:

```
POST /artifacts/{task_id}       write artifact (multipart)
GET  /artifacts/{task_id}/{key} read artifact
POST /progress/{task_id}        emit progress event
POST /signals/{task_id}         emit signal
GET  /health                    liveness check
```

Agents authenticate with a task-scoped JWT passed via `MONET_TOKEN` env var.
Gateway validates the JWT and routes to configured backend stores.

**Rejected alternative: worker endpoints for local, gateway for remote.**
This creates two code paths and an implicit mode switch when the user adds
a cloud-push pool. The system would need to detect deployment topology to
choose the right path. Always-gateway eliminates this — one path, one auth
model, one contract for all agents in all deployments.

### D2: Gateway deployment modes

The gateway is the same code in every mode. Deployment context determines
how it runs:

| Deployment | Gateway runs as | Reachable via |
|---|---|---|
| `monet dev` | Embedded in worker process | `http://localhost:{port}` |
| Local + cloud push | Container in Docker Desktop + Cloudflare Tunnel | `https://{id}.trycloudflare.com` |
| Self-hosted production | Standalone container behind LB | `https://dp.example.com` |
| Managed data plane (SaaS) | Monet-hosted service | `https://dp.monet.example` |

For `monet dev`, the gateway starts automatically as part of the worker
process. No extra configuration. The JWT signing key is a known dev constant
(like Django's `SECRET_KEY` in development) so local agents get valid tokens
without real key management.

### D3: Cloudflare Tunnel for local + cloud push

For users who run workers locally and burst to cloud, `cloudflared` quick
tunnels provide a public URL into the Docker Desktop network with zero
account setup:

```
Docker Desktop network
+-- monet worker (claims tasks, dispatches to CloudRun)
+-- gateway container (receives agent writes)
+-- postgres container (progress store)
+-- artifact volume (local storage)
+-- cloudflared tunnel (public URL -> gateway)
+-- openclaw container (local agent)

CloudRun (external network)
+-- burst agent -> POSTs to tunnel URL -> gateway -> local stores
```

Quick tunnels (`trycloudflare.com`) are free, require no authentication,
and provide a random hostname. Named tunnels (stable hostname) require a
free Cloudflare account.

Enabled via config:

```toml
[gateway]
tunnel = "cloudflare"
```

Or CLI flag: `monet dev --tunnel`

The compose stack includes a `cloudflared` sidecar when tunnel is enabled.

### D4: Pool-scoped service configuration

Service endpoints for backend stores are configured per pool, not in a
global `[services]` section. Rationale:

- Different pools may be in different networks needing different endpoints
- Pool config is infrastructure config owned by whoever operates the pool
- In SaaS mode, customer-owned service endpoints should not live in
  SaaS-owned server config
- Pool config naturally moves with the pool definition in future SaaS phase

```toml
[pools.local-dev]
backend = "in_process"
# No services override — gateway uses defaults (local filesystem, SQLite)

[pools.cloud-burst]
backend = "cloudrun"
project = "my-project"
region = "us-central1"
job = "monet-worker"
gateway = "https://dp.example.com"   # gateway URL for agents in this pool
```

When a pool specifies `gateway`, the worker injects that URL as
`MONET_GATEWAY_URL` when dispatching to cloud containers. When unset,
defaults to the worker's embedded gateway on localhost.

### D5: Task-scoped JWT authentication

Worker mints a JWT when dispatching a task:

```python
token = jwt.encode({
    "task_id": record.task_id,
    "pool": pool.name,
    "scopes": ["artifact:write", "progress:write", "signal:emit"],
    "exp": now + pool.task_timeout_s + buffer,
}, signing_key)
```

Passed to agents as `MONET_TOKEN` env var. Gateway validates JWT, checks
scopes, enforces task-level isolation (agent for task A cannot write to
task B's namespace).

Short-lived tokens (expire with task timeout). No long-lived credentials
in agent containers. Credential rotation is one config change on the
gateway, not N changes across N agent deployments.

### D6: MCP sidecar and CLI as gateway clients

MCP sidecar tools are thin HTTP clients. Each tool reads `MONET_GATEWAY_URL`
and `MONET_TOKEN` from env vars, makes a POST with bearer auth:

```
write_artifact(key, content) -> POST /artifacts/{task_id}
emit_progress(event)         -> POST /progress/{task_id}
emit_signal(signal)          -> POST /signals/{task_id}
```

No backend SDKs, no credential handling in the sidecar. Same tool works
against localhost gateway, tunnel URL, production gateway, or managed DP.

CLI alternative for agents that prefer bash tools:

```bash
monet artifact write result.json    # reads MONET_GATEWAY_URL, MONET_TOKEN
monet progress emit "step 3 of 5"
monet signal emit needs_review
```

Any agent runtime (Python, Node, Go, Rust, shell script) can participate
via raw HTTP POST with bearer token. No monet SDK dependency required.

## Enterprise service topology

```
Control Plane (SaaS or self-hosted)
+-- Task routing, run state, metadata, UI
+-- No user data transits here
       |
       | outbound only (workers poll CP)
       |
Customer Infrastructure
+-- Data Plane Gateway (public HTTPS, JWT auth)
|   +-- Routes to backend stores
|   +-- Audit log
|   +-- Single credential store
|       |         |          |
|     S3/GCS   Postgres   OTel collector
|     (private, only gateway has credentials)
|
+-- Workers (any network, outbound-only)
|   +-- laptop, VPS, K8s
|
+-- Agents (any network, outbound to gateway)
    +-- local subprocess, Docker, CloudRun, ECS, VPS
```

All traffic is outbound from workers and agents. Only the gateway accepts
inbound connections. One ingress point to secure (TLS, JWT, rate limiting).

## Consequences

**Positive:**
- One code path for all agents in all deployments — no mode switching
- Credential isolation — backend secrets in one location
- Any agent runtime participates via HTTP + bearer token
- Adoption ramp is smooth: `monet dev` (embedded gateway) to cloud push
  (add tunnel) to production (deploy gateway) to managed DP (use ours)
- MCP sidecar is trivially simple — thin HTTP client

**Negative:**
- HTTP hop for local agents (~1ms to localhost, negligible but nonzero)
- Gateway is a component to deploy for cross-network scenarios
- JWT signing key management (mitigated: dev uses constant, production
  uses standard secret management)
- Cloudflare Tunnel dependency for the local + cloud push convenience
  (optional, user can deploy gateway with their own ingress instead)

## Relationship to other components

- **Phase 4 of worker composition plan** becomes "data plane gateway,
  embedded mode" — same routes, always present, even in `monet dev`
- **Phase 8 (server webhooks)** removed entirely (see ADR-007)
- **IAP reverse proxy** from openclaw-mvp.md is subsumed by the gateway —
  same role (authenticated ingress into data plane), now with a defined
  API contract
- **Artifact store, progress store, OTel** remain behind their existing
  protocol abstractions — gateway routes to them, doesn't replace them

## Open questions

1. **OTel forwarding:** Should the gateway proxy OTel spans, or should
   agents export OTLP directly to a configured collector? Direct OTLP
   export is standard practice and avoids gateway bandwidth for trace
   data. Likely answer: OTel is direct, gateway handles artifacts +
   progress + signals only.

2. **Gateway horizontal scaling:** For high-throughput deployments, can
   multiple gateway instances run behind a load balancer? Yes — gateway
   is stateless, routes to shared backends. Standard horizontal scaling.

3. **Managed data plane product scope:** Hosting the gateway + provisioning
   per-customer storage is a second product. Scoping and timeline are
   separate from the SDK work.

4. **Prefect-style blocks:** Named, typed service profiles (beyond raw
   pool config) are a natural evolution if users need to share service
   configs across pools. Deferred — pool-scoped config is sufficient
   for now.

## References

- ADR-007 (polling for result delivery, removed webhooks)
- openclaw-mvp.md section on identity-aware proxy
- Prefect blocks: typed infrastructure abstractions for service access
- Dagster resources: dependency-injected service configurations
- deployment-scenarios.md for S1-S5 topology descriptions
