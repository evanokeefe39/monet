# ADR-007 — Cloud-push result delivery via polling, not webhooks

Status: **accepted**
Date: 2026-05-04

## Context

The worker composition plan (Phase 8) originally specified webhook callback
endpoints on the server for cloud-push agents (CloudRun, ECS) to POST results
back after execution. During design review, we identified fundamental problems
with this approach across monet's deployment scenarios.

The core tension: webhook receivers must be reachable from the sender's network.
When the sender is a CloudRun job in GCP and the receiver is a worker on a
laptop behind NAT, there is no clean solution — only workarounds (tunnels,
reverse proxies, E2E encryption through control plane).

We investigated how every major production orchestrator handles this.

## Research findings

**Prefect:** Workers poll Prefect Cloud API for work via long-polling. Results
are pushed back via outbound HTTPS POST to the API. No inbound connectivity
required on workers. Result data goes to user-configured storage (S3, GCS).
Cloud only stores metadata pointers.

**Temporal:** Workers long-poll the Temporal server via gRPC. Tasks complete via
gRPC call back to the server. The server owns persistence. All communication
is worker-initiated (outbound).

**Windmill:** Workers poll a Postgres-backed queue. Results written directly to
Postgres. All worker communication is outbound.

**GitHub Actions self-hosted runners:** Identical pattern to Prefect. Runner
opens long-poll HTTPS connection to GitHub servers, receives jobs, executes,
posts results back via outbound HTTPS.

**Universal pattern:** Worker-pushes-result-to-server over outbound HTTP/gRPC.
No production orchestrator uses webhooks from cloud providers for result
delivery. No production orchestrator requires inbound connectivity on workers.

## Decision

Cloud-push result delivery uses **worker-managed poll-and-collect**, not
webhooks.

The flow:

1. Worker claims task from server (existing pattern)
2. Worker dispatches to CloudRun/ECS via cloud API (existing pattern)
3. Worker polls cloud API for job completion (`GetExecution`, `DescribeTasks`)
4. On completion, worker retrieves result from the data plane gateway
5. Worker calls `queue.complete()` (existing pattern)

The worker owns the full lifecycle: dispatch, poll, collect, complete. No
callback URLs, no HMAC validation, no webhook receivers for result delivery.

## ExecutionBackend protocol change

Cloud-push backends gain polling methods:

```python
class ExecutionBackend(Protocol):
    async def start(self, spec: ContainerSpec, env: dict[str, str]) -> Endpoint: ...
    async def poll_status(self, endpoint: Endpoint) -> JobStatus: ...
    async def stop(self, endpoint: Endpoint, grace_period_s: float) -> None: ...
    async def kill(self, endpoint: Endpoint) -> None: ...
```

`poll_status` returns a `JobStatus` (running, succeeded, failed, unknown).
Worker polls at configurable intervals (default 5s) with exponential backoff
ceiling. Cloud APIs have rate limits but cloud jobs typically run minutes,
so polling at 5-10s intervals is well within limits.

`liveness_check` and `readiness_check` from the original plan are removed
for cloud-push backends. These concepts don't apply — the cloud provider
manages the container lifecycle.

## Consequences

**Positive:**
- Eliminates entire class of NAT traversal / ingress / proxy problems for
  result delivery
- Workers remain outbound-only — no firewall rules, no inbound ports
- Single result delivery path (worker collects) — one place to debug
- Cloud APIs have built-in retry and status persistence
- Matches industry-proven pattern from Prefect, Temporal, Windmill

**Negative:**
- Adds polling latency (5-10s between checks). Acceptable for cloud jobs
  that run minutes to hours. Not suitable for sub-second execution.
- Cloud API rate limits constrain poll frequency. Mitigated by exponential
  backoff and configurable intervals.
- Worker must stay alive during cloud job execution. If worker dies mid-poll,
  the task needs recovery (see below).

## Orphan recovery

If a worker crashes while polling a cloud job, the task's lease expires and
another worker reclaims it. The task record stores the cloud execution ID
(endpoint metadata). The new worker can resume polling from where the previous
worker left off. The cloud job itself is unaffected — it runs to completion
regardless of worker state.

## What this replaces

- Phase 8 (server webhook routes) from the original worker composition plan
  is removed entirely
- The `Callback` transport type from the target architecture doc is removed
- `WorkerClient.complete()` as a callback mechanism from cloud containers is
  no longer needed for result delivery (it remains for worker-to-server
  communication)

## In-flight progress from cloud-push agents

This ADR covers result delivery only. In-flight progress, signals, and
artifacts from cloud-push agents are addressed separately in ADR-008 (data
plane gateway). Cloud-push agents POST progress events to the gateway during
execution. Final result delivery is via polling.

## Rejected alternatives

**Webhook on server:** Requires the server to be reachable from cloud
containers. In split-plane (S5), this means user data transits vendor
infrastructure. In self-hosted, the server may not be reachable from cloud
provider networks.

**Webhook on worker:** Workers are behind NAT in common deployment scenarios
(laptop, VPS without public IP). Requires tunneling or reverse proxy.

**Webhook on reverse proxy with queueing:** Adds a new service to deploy and
operate. Solves reachability but introduces a new failure mode (proxy down =
lost results). Overengineered for the problem.

**E2E encryption through control plane:** Solves split-plane data concern but
creates bandwidth costs on SaaS, requires size limits, and still requires the
CP to be reachable from cloud containers.

**Message broker (NATS) for results:** Adds operational dependency with no
functional gain over existing Redis Streams. Every production orchestrator
uses HTTP/gRPC push, not a broker, for result delivery.

## References

- Prefect worker architecture: workers are outbound-only, poll for work, push results
- Temporal worker model: long-poll gRPC, complete tasks via gRPC callback
- GitHub Actions self-hosted runners: outbound HTTPS polling
- Original worker composition plan Phase 8 (superseded by this ADR)
