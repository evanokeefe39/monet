# ADR: Work Dispatch and Progress Streaming Architecture

**Status:** Proposed
**Date:** 2026-04-14
**Context:** LangGraph/Aegra server with graph nodes that delegate work to external services

---

## Context

The system uses LangGraph for durable orchestration of graphs, served via Aegra (an open-source Agent Protocol server for LangGraph). Nodes on those graphs delegate compute-intensive or IO-bound workloads to external services, including serverless infrastructure like Google Cloud Run. The system is deployed as a managed SaaS — clients post requests to the SaaS API, which triggers graph execution, which in turn dispatches work to external compute.

The expected scale is up to 1,000 concurrent users creating and triggering workflows frequently, with IO-bound workloads dominating.

Two distinct communication patterns exist within this architecture:

1. **Work dispatch and completion** — a command ("go do this work") sent to an external service, and a signal ("it's done") returned when complete.
2. **Progress streaming** — high-frequency, ephemeral status updates emitted by services while work is in progress, relayed to the client in real time.

These patterns have fundamentally different requirements in terms of durability, latency, and delivery guarantees.

---

## Decision

Use two dispatch strategies depending on the target infrastructure:

1. **Redis Streams** for dispatching to self-managed services where consumer groups and backpressure management are needed.
2. **Direct cloud provider API calls** (Prefect-style push) for dispatching to serverless infrastructure (Cloud Run, ECS Fargate, ACI), where the provider handles queuing and scaling.

For progress streaming, use **Aegra's built-in SSE streaming** with `get_stream_writer()` and `custom` stream mode. Cloud Run containers publish progress to Redis Pub/Sub, and the graph node relays it through Aegra's existing SSE connection to the client. This eliminates the need for a separate progress streaming endpoint or infrastructure.

Do not use a database-polling pattern (Prefect-style) for work dispatch.

---

## Alternatives Considered

### Database Polling (Prefect-style)

Prefect's work pool implementation was examined at the source level (`src/prefect/workers/base.py`, `src/prefect/server/api/workers.py`). The mechanism is:

- Flow runs are stored as rows in Postgres (Cloud) or SQLite (OSS) with a `Scheduled` state.
- Workers run a polling loop (default 15s interval) that makes an HTTP POST to `/work_pools/{name}/get_scheduled_flow_runs`.
- The server-side handler executes a filtered SELECT against flow runs by state, time window, and work queue membership.
- Concurrency control is enforced at query time — the server counts active runs and only returns enough to stay under the configured limit.
- Work queue priority is SQL ordering, not a priority queue data structure.
- There is no message broker, no in-memory queue, no pub/sub — it is a database table being polled via REST.

This pattern is well-suited for orchestration workloads (coordinating workflows with complex dependencies, retries, and observability). However, it requires building custom polling logic, handling concurrent consumers without double-dispatch (row-level locking or optimistic concurrency), implementing acknowledgement tracking, timeout-based failure detection, and table cleanup. Each of these is individually simple but collectively amounts to a bespoke work queue built on infrastructure not designed for it.

For the IO-bound workloads in this system, the database write rate from state transitions would be low relative to wall-clock time, so Prefect's pattern would be functionally adequate at the expected scale. The concern is not performance but unnecessary implementation complexity.

### Redis Streams (Selected)

Redis Streams provides the required primitives as first-class operations:

- `XADD` — publish a work item to a stream
- `XREADGROUP` — distribute work across consumers without duplication (consumer groups)
- `XACK` — acknowledge completion
- `XPENDING` — find messages delivered but never acknowledged (crash recovery)
- `XCLAIM` — reassign stuck messages to a different consumer after a timeout

This eliminates the need to build polling logic, double-dispatch prevention, acknowledgement tracking, or failure detection. The consumer group mechanism solves work distribution at the infrastructure level.

### Full Message Brokers (Kafka, RabbitMQ, SQS)

These provide stronger guarantees (Kafka's durable partitioned log, RabbitMQ's protocol-level delivery guarantees) but introduce operational complexity disproportionate to the requirements. The workloads are IO-bound with moderate dispatch volume — the advanced features of these systems are not needed.

---

## Architecture

### End-to-End Request Flow

```
Client (browser/app)
    |
    |-- POST to SaaS API with task parameters
    |-- Opens SSE connection to Aegra for streaming
    |
    v
SaaS API / Aegra Server
    |
    |-- Creates thread, starts graph run
    |-- SSE connection held open, streaming events to client
    |
    v
LangGraph Graph Execution
    |
    |-- Node determines work needs external compute
    |-- Dispatches to Cloud Run (or self-managed service)
    |-- Relays progress back through get_stream_writer()
    |-- Returns result to graph state on completion
    |
    v
Aegra SSE → Client receives progress + final result
```

### Dispatch Strategy 1: Self-Managed Services (Redis Streams)

For services running on infrastructure we control (Kubernetes pods, VMs, long-running containers), Redis Streams provides durable dispatch with consumer groups.

```
LangGraph Node
    |
    |-- XADD to stream (keyed by service type, includes correlation ID)
    |-- Checkpoint graph state with correlation ID
    |-- Node suspends
    |
    v
Redis Stream (consumer group per service type)
    |
    |-- Service instance reads via XREADGROUP
    |-- Processes work
    |-- XACK on completion
    |-- Publishes completion message to response stream
    |
    v
Response Listener
    |
    |-- Reads completion from response stream
    |-- Triggers LangGraph resumption with correlation ID
```

### Dispatch Strategy 2: Cloud Push (Direct API Call)

For serverless infrastructure (Cloud Run, ECS Fargate, ACI), bypass Redis Streams entirely. The graph node calls the cloud provider API directly, mirroring Prefect's push work pool pattern. The provider handles queuing and scaling on its side.

```
LangGraph Node
    |
    |-- Calls Cloud Run Jobs API directly
    |-- Passes correlation ID, Redis credentials, Aegra callback URL as env vars
    |-- Node behavior depends on streaming requirements (see below)
    |
    v
Cloud Run Container
    |
    |-- Executes workload
    |-- Publishes progress to Redis Pub/Sub (progress:{correlation_id})
    |-- On completion: publishes to Redis Streams completion queue
    |      OR calls Aegra API directly to resume graph
```

Redis Streams adds unnecessary indirection here because the cloud provider API is already a fire-and-forget submission — the provider manages its own job queue and backpressure internally.

### Progress Streaming via Aegra SSE

Aegra provides built-in SSE streaming with a `custom` stream mode. Graph nodes can emit arbitrary data to the client using `get_stream_writer()`. This is the primary mechanism for progress streaming.

The client opens a single SSE connection to Aegra and receives all event types — graph state updates, LLM tokens, and custom progress data — multiplexed through the same connection. No separate progress endpoint is needed.

Aegra also stores streaming events in its database for replay. If the client disconnects, it can reconnect with a `Last-Event-ID` header and receive all events from where it left off. Events are retained for 1 hour after the run completes.

#### Short-to-Medium Jobs (seconds to a few minutes): Node Stays Alive

The graph node starts the Cloud Run job, then enters an async loop subscribing to Redis Pub/Sub for progress. Each message is relayed to the client through `get_stream_writer()`. The node completes when it receives the final result.

```
Client ←——SSE——→ Aegra Server (node running, holding SSE open)
                    |
                    |  get_stream_writer() ← reads from Redis Pub/Sub
                    |                              ↑
                    |                           publish
                    |                              |
                    |——— Cloud Run API ———→ Cloud Run container
                         (creates job)     (does work, publishes progress)
```

```python
async def delegate_node(state):
    writer = get_stream_writer()
    correlation_id = str(uuid4())

    # kick off Cloud Run job with correlation ID and Redis credentials
    start_cloud_run_job(
        correlation_id=correlation_id,
        task=state["task"],
        env={
            "CORRELATION_ID": correlation_id,
            "REDIS_URL": settings.redis_url,
            "REDIS_TOKEN": settings.redis_token,
        },
    )

    # subscribe to progress channel and relay to client
    async for message in redis_subscribe(f"progress:{correlation_id}"):
        if message["type"] == "complete":
            return {"result": message["data"]}
        writer({"progress": message["data"]})
```

The Cloud Run container publishes progress via Redis Pub/Sub (or Upstash REST API if no VPC peering):

```python
# inside Cloud Run container
redis.publish(f"progress:{correlation_id}", json.dumps({
    "type": "progress",
    "data": {"step": "processing", "percent": 45}
}))

# on completion
redis.publish(f"progress:{correlation_id}", json.dumps({
    "type": "complete",
    "data": {"output": result}
}))
```

The client consumes this transparently through Aegra's standard streaming:

```python
async for chunk in client.runs.stream(
    thread_id=thread_id,
    assistant_id="agent",
    input={"messages": [{"type": "human", "content": "Process this"}]},
    stream_mode=["custom", "values"],
):
    if chunk.event == "custom":
        update_progress_bar(chunk.data)
```

**Tradeoff:** The graph node occupies an Aegra worker thread for the job's duration. For IO-bound work this is a lightweight async coroutine consuming minimal resources. At 1,000 concurrent users with jobs completing in minutes, this is acceptable.

#### Long-Running Jobs (tens of minutes to hours): Node Suspends

For very long jobs, holding a worker thread open is wasteful. The node checkpoints and suspends. This breaks the Aegra SSE streaming path — the client's SSE connection closes when the node suspends.

In this case, progress streaming falls back to a separate channel:

```
Client ←——SSE (1)——→ Aegra (initial request, returns quickly)
Client ←——SSE (2)——→ Thin progress proxy ←—— Redis Pub/Sub ←—— Cloud Run
                                                                    |
Aegra ←——completion callback or Redis Streams——————————————————————-+
    |
    |-- Resumes graph with correlation ID
    |-- Client reconnects to Aegra SSE for final result
```

This requires a separate lightweight SSE endpoint that subscribes to Redis Pub/Sub and forwards to the client. More infrastructure, but frees the worker thread.

**Recommendation:** Start with the node-stays-alive pattern. Only move to the suspend pattern if job durations prove long enough to cause worker thread pressure at scale.

### Graph Resumption (Suspend Pattern Only)

When using the suspend pattern, the response listener is a lightweight process consuming from the Redis Streams completion queue. When a completion message arrives, it calls Aegra's API to resume the suspended graph with the correlation ID. This keeps LangGraph/Aegra purely as an orchestrator — it does not run a consumer loop itself.

---

## Redis Streams Integration (Self-Managed Services Only)

Per-service integration is minimal (approximately 20-30 lines of code per service). This section applies to Dispatch Strategy 1 only — cloud push dispatch does not use Redis Streams for the initial dispatch.

**Publisher (graph node side):**
```
XADD service:{type} * correlation_id {id} payload {json}
```

**Consumer (service side):**
```
XREADGROUP GROUP {service_group} {consumer_id} COUNT 1 BLOCK 5000 STREAMS service:{type} >
... process work ...
XACK service:{type} {service_group} {message_id}
XADD completions * correlation_id {id} result {json}
```

**Failure recovery:**
```
XPENDING service:{type} {service_group} - + 10
XCLAIM service:{type} {service_group} {consumer_id} {min_idle_ms} {message_id}
```

---

## Persistence and Durability

Redis Streams are durable with AOF persistence enabled. Configuration options:

- `appendfsync everysec` — at most one second of data loss on crash (recommended for most workloads)
- `appendfsync always` — zero loss, lower throughput
- Managed Redis instances (Upstash, Redis Cloud, ElastiCache) handle persistence configuration automatically

For work dispatch messages, the acceptable loss window is `everysec`. If a crash occurs, at most one second of dispatched-but-unacknowledged work is lost, and the graph nodes that dispatched it will time out and can be retried.

---

## Cost Estimate

For work dispatch at the expected scale (1,000 users, IO-bound workloads, moderate dispatch frequency), Redis infrastructure costs are minimal:

- **Self-hosted:** negligible beyond existing compute costs (50-100MB memory footprint)
- **Upstash (serverless):** free tier covers prototyping (256MB, 500K commands/month); pay-as-you-go at $0.20/100K commands covers production at single-digit dollars/month
- **Redis Cloud:** free 30MB tier for testing; paid from ~$70/month for managed HA
- **Cloud-managed (ElastiCache, Memorystore):** from ~$15-25/month for smallest instances

Upstash is recommended for initial deployment due to zero operational overhead and negligible cost at the expected command volume for dispatch/completion signals.

---

## Relationship to Prefect's Architecture

Prefect was evaluated as a potential orchestration layer. At the expected scale (1,000 users with frequent workflow triggers), Prefect's database-backed state management would be functionally adequate. The IO-bound nature of the workloads means low state-transition write rates relative to wall-clock time.

However, LangGraph/Aegra already provides the durable orchestration needed (graph state persistence, interrupts, resumption, SSE streaming). Adding Prefect would introduce a second orchestration layer without solving the work dispatch problem, which Prefect addresses with the same database-polling pattern described above.

The cloud push dispatch pattern in this architecture is directly inspired by Prefect's push work pool implementation. Prefect's server-side Docket component makes a direct HTTP call to the cloud provider API (e.g., Cloud Run Jobs API) with stored credentials when a run becomes schedulable. We adopt the same approach — the graph node calls the cloud provider API directly, passing credentials and correlation metadata as environment variables. The difference is that our progress streaming flows through Aegra's SSE rather than through Prefect's API-based state reporting.

---

## Consequences

**Positive:**
- Single SSE connection from client to Aegra carries all event types (graph state, LLM tokens, custom progress) — no separate streaming infrastructure for the primary use case
- Aegra's built-in event replay and `Last-Event-ID` reconnection provides client-side durability without custom implementation
- Cloud push dispatch mirrors a proven pattern (Prefect's push work pools) with zero intermediary infrastructure
- Redis Streams for self-managed services provides consumer groups and backpressure without custom queue logic
- Clear decision boundary: job duration determines whether node stays alive (simple) or suspends (complex but resource-efficient)
- Independent consumer scaling for self-managed services — add instances to increase throughput without touching orchestration

**Negative:**
- Adds Redis as an infrastructure dependency (required for both Pub/Sub progress relay and Streams dispatch to self-managed services)
- Node-stays-alive pattern occupies an Aegra worker thread for the job's duration — acceptable for short/medium jobs but requires monitoring at scale
- Less built-in observability than Prefect's model (no automatic historical query of dispatch failures) — would need to build this separately if needed
- Cloud Run containers need outbound access to Redis for progress publishing (trivial with Upstash REST API, may require VPC configuration for self-hosted Redis)

**Neutral:**
- If job durations grow, migration from node-stays-alive to suspend pattern is a per-node change, not an architectural overhaul — the Redis Pub/Sub progress channel works for both patterns
- The suspend pattern requires a separate thin SSE proxy for progress, adding a small infrastructure component — but this only applies to long-running jobs