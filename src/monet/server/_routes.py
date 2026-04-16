"""REST API routes for the monet orchestration server."""

from __future__ import annotations

import logging
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from monet import get_artifacts
from monet._ports import MAX_INLINE_PAYLOAD_BYTES
from monet.core.manifest import AgentCapability, AgentManifest
from monet.queue import TaskQueue
from monet.queue.backends.redis_streams import RedisStreamsTaskQueue
from monet.server._auth import require_api_key, require_task_auth
from monet.server._deployment import DeploymentStore
from monet.types import AgentResult, Signal, build_artifact_pointer

_log = logging.getLogger("monet.server.routes")

__all__ = ["router"]


# -- Dependency injection helpers ------------------------------------------


def get_queue(request: Request) -> TaskQueue:
    """Retrieve the task queue from application state."""
    return request.app.state.queue  # type: ignore[no-any-return]


def get_deployments(request: Request) -> DeploymentStore:
    """Retrieve the deployment store from application state."""
    return request.app.state.deployments  # type: ignore[no-any-return]


def get_manifest(request: Request) -> AgentManifest:
    """Retrieve the agent manifest from application state."""
    return request.app.state.manifest  # type: ignore[no-any-return]


# Type aliases for annotated dependencies
Queue = Annotated[TaskQueue, Depends(get_queue)]
Deployments = Annotated[DeploymentStore, Depends(get_deployments)]
Manifest = Annotated[AgentManifest, Depends(get_manifest)]


# -- Request / Response schemas --------------------------------------------


class WorkerRegisterRequest(BaseModel):
    """Body for ``POST /api/v1/worker/register``."""

    pool: str
    capabilities: list[dict[str, str]]
    worker_id: str


class WorkerRegisterResponse(BaseModel):
    """Response for ``POST /api/v1/worker/register``."""

    deployment_id: str


class HeartbeatRequest(BaseModel):
    """Body for ``POST /api/v1/worker/heartbeat``."""

    worker_id: str
    pool: str
    capabilities: list[dict[str, str]] | None = None


class TaskCompleteRequest(BaseModel):
    """Body for ``POST /api/v1/tasks/{task_id}/complete``."""

    success: bool
    output: str | dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    trace_id: str = ""
    run_id: str = ""


class TaskFailRequest(BaseModel):
    """Body for ``POST /api/v1/tasks/{task_id}/fail``."""

    error: str


class CreateDeploymentRequest(BaseModel):
    """Body for ``POST /api/v1/deployments``."""

    pool: str
    capabilities: list[dict[str, str]]


class PoolClaimRequest(BaseModel):
    """Body for ``POST /api/v1/pools/{pool}/claim``."""

    consumer_id: str
    block_ms: int = 5000


class HealthResponse(BaseModel):
    """Response for ``GET /api/v1/health``."""

    status: str
    workers: int
    queued: int
    redis: str | None = None


# -- Router ----------------------------------------------------------------


router = APIRouter(prefix="/api/v1")


@router.post(
    "/worker/register",
    response_model=WorkerRegisterResponse,
    dependencies=[Depends(require_api_key)],
)
async def register_worker(
    body: WorkerRegisterRequest,
    deployments: Deployments,
    manifest: Manifest,
) -> WorkerRegisterResponse:
    """Register a worker and its capabilities."""
    caps = cast("list[AgentCapability]", body.capabilities)
    deployment_id = await deployments.create(body.pool, caps)
    await deployments.register_worker(deployment_id, body.worker_id)
    for cap in body.capabilities:
        manifest.declare(
            cap.get("agent_id", ""),
            cap.get("command", ""),
            description=cap.get("description", ""),
            pool=cap.get("pool", body.pool),
            worker_id=body.worker_id,
        )
    _log.info(
        "worker.register worker=%s pool=%s capabilities=%d deployment=%s",
        body.worker_id,
        body.pool,
        len(body.capabilities),
        deployment_id,
    )
    return WorkerRegisterResponse(deployment_id=deployment_id)


@router.post(
    "/worker/heartbeat",
    dependencies=[Depends(require_api_key)],
)
async def heartbeat(
    body: HeartbeatRequest,
    deployments: Deployments,
    manifest: Manifest,
) -> dict[str, str]:
    """Update heartbeat for a worker.

    If capabilities are included, reconciles the manifest: declares
    new/updated capabilities for this worker and removes any the worker
    no longer advertises.
    """
    await deployments.heartbeat(body.worker_id)

    if body.capabilities is not None:
        caps = [
            AgentCapability(
                agent_id=c.get("agent_id", ""),
                command=c.get("command", ""),
                description=c.get("description", ""),
                pool=c.get("pool", body.pool),
            )
            for c in body.capabilities
        ]
        manifest.reconcile_worker(body.worker_id, caps)

        # Also update the deployment record's capabilities.
        await deployments.update_capabilities(body.worker_id, body.capabilities)

    _log.info(
        "worker.heartbeat worker=%s pool=%s capabilities=%s",
        body.worker_id,
        body.pool,
        len(body.capabilities) if body.capabilities is not None else "unchanged",
    )
    return {"status": "ok"}


@router.get(
    "/tasks/claim/{pool}",
    dependencies=[Depends(require_api_key)],
)
async def claim_task(
    pool: str,
    response: Response,
    queue: Queue,
) -> dict[str, Any] | None:
    """Claim the next pending task in a pool (legacy non-blocking).

    Kept for RemoteQueue backwards compatibility. New workers should
    use ``POST /api/v1/pools/{pool}/claim`` which honours ``block_ms``
    and ``consumer_id``.
    """
    record = await queue.claim(pool, consumer_id="server", block_ms=0)
    if record is None:
        response.status_code = 204
        return None
    return dict(record)


@router.post(
    "/pools/{pool}/claim",
    dependencies=[Depends(require_api_key)],
)
async def claim_from_pool(
    pool: str,
    body: PoolClaimRequest,
    response: Response,
    queue: Queue,
) -> dict[str, Any] | None:
    """Claim one task from the pool, server-blocking up to ``block_ms``.

    The server issues ``XREADGROUP ... BLOCK block_ms`` (or the memory
    equivalent) so the worker's HTTP request waits until a task lands
    or the timeout elapses. Returns the task record on success or 204
    No Content on timeout.
    """
    record = await queue.claim(
        pool, consumer_id=body.consumer_id, block_ms=body.block_ms
    )
    if record is None:
        response.status_code = 204
        return None
    return dict(record)


@router.post(
    "/tasks/{task_id}/complete",
    dependencies=[Depends(require_task_auth)],
)
async def complete_task(
    task_id: str,
    body: TaskCompleteRequest,
    queue: Queue,
) -> dict[str, str]:
    """Post a successful result for a claimed task."""
    result = AgentResult(
        success=body.success,
        output=body.output,
        artifacts=tuple(build_artifact_pointer(a) for a in body.artifacts),
        signals=tuple(
            Signal(
                type=s.get("type", ""),
                reason=s.get("reason", ""),
                metadata=s.get("metadata"),
            )
            for s in body.signals
        ),
        trace_id=body.trace_id,
        run_id=body.run_id,
    )
    await queue.complete(task_id, result)
    return {"status": "ok"}


@router.post(
    "/tasks/{task_id}/fail",
    dependencies=[Depends(require_task_auth)],
)
async def fail_task(
    task_id: str,
    body: TaskFailRequest,
    queue: Queue,
) -> dict[str, str]:
    """Post a failure for a claimed task."""
    await queue.fail(task_id, body.error)
    return {"status": "ok"}


@router.post(
    "/tasks/{task_id}/progress",
    status_code=202,
    dependencies=[Depends(require_task_auth)],
)
async def post_progress(
    task_id: str,
    body: dict[str, Any],
    queue: Queue,
    request: Request,
) -> dict[str, str]:
    """Fire-and-forget progress event from a remote worker.

    Rejects bodies larger than ``MAX_INLINE_PAYLOAD_BYTES`` (413). The
    server publishes to Redis Pub/Sub (or the in-memory fan-out); lost
    publishes are acceptable per ADR §progress-flow loss budget.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            size = int(content_length)
        except ValueError as exc:
            raise HTTPException(400, "Invalid Content-Length") from exc
        if size > MAX_INLINE_PAYLOAD_BYTES:
            raise HTTPException(
                413,
                f"Progress payload {size} bytes exceeds "
                f"MAX_INLINE_PAYLOAD_BYTES={MAX_INLINE_PAYLOAD_BYTES}",
            )
    await queue.publish_progress(task_id, body)
    return {"status": "accepted"}


@router.get(
    "/deployments",
    dependencies=[Depends(require_api_key)],
)
async def list_deployments(
    deployments: Deployments,
    pool: str | None = None,
) -> list[dict[str, Any]]:
    """List active deployments, optionally filtered by pool."""
    records = await deployments.get_active(pool)
    return [dict(r) for r in records]


@router.post(
    "/deployments",
    status_code=201,
    dependencies=[Depends(require_api_key)],
)
async def create_deployment(
    body: CreateDeploymentRequest,
    deployments: Deployments,
) -> dict[str, str]:
    """Create a deployment record."""
    caps = cast("list[AgentCapability]", body.capabilities)
    deployment_id = await deployments.create(body.pool, caps)
    return {"deployment_id": deployment_id}


@router.get("/agents")
async def list_agents(manifest: Manifest) -> list[dict[str, Any]]:
    """List every capability declared in the agent manifest.

    Returns one entry per ``(agent_id, command)`` pair with its pool
    assignment and optional description. Used by ``MonetClient.list_capabilities``
    so clients can discover user-defined agents at runtime (chat REPL
    dynamic ``/<agent_id>:<command>`` dispatch, direct invocation via
    ``monet run <agent>:<command>``).
    """
    return [dict(cap) for cap in manifest.capabilities()]


class InvokeAgentRequest(BaseModel):
    """Body for ``POST /api/v1/agents/{agent_id}/{command}/invoke``."""

    task: str = ""
    context: list[dict[str, Any]] | None = None
    skills: list[str] | None = None


@router.post(
    "/agents/{agent_id}/{command}/invoke",
    dependencies=[Depends(require_api_key)],
)
async def invoke_agent_endpoint(
    agent_id: str,
    command: str,
    body: InvokeAgentRequest,
) -> dict[str, Any]:
    """Run a single ``agent_id:command`` invocation and return the result.

    Wraps the orchestration-side ``invoke_agent`` primitive so clients
    can drive a single capability without composing a graph. Fails with
    400 if the capability isn't in the manifest.
    """
    from monet.orchestration._invoke import invoke_agent

    try:
        result = await invoke_agent(
            agent_id,
            command=command,
            task=body.task,
            context=body.context,
            skills=body.skills,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # AgentResult is a TypedDict; cast the dict-view to the endpoint's
    # return type for mypy.
    return cast("dict[str, Any]", result)


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str) -> Response:
    """Stream the raw bytes of an artifact by id.

    Lets a chat UI render a clickable deep link (``…/api/v1/artifacts/<id>``)
    so the operator can open a plan's full work brief in the browser.
    Returns the artifact content with its recorded ``content_type`` and
    a ``Content-Disposition`` hinting at the artifact key.
    """
    try:
        content, metadata = await get_artifacts().read(artifact_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"artifact {artifact_id} not found"
        ) from exc
    except Exception as exc:
        _log.exception("artifact read failed for %s", artifact_id)
        raise HTTPException(
            status_code=500, detail=f"artifact read failed: {exc}"
        ) from exc
    media_type = str(metadata.get("content_type") or "application/octet-stream")
    key = str(metadata.get("key") or artifact_id)
    headers = {"Content-Disposition": f'inline; filename="{key}"'}
    return Response(content=content, media_type=media_type, headers=headers)


@router.get("/artifacts/{artifact_id}/view", response_class=HTMLResponse)
async def view_artifact(artifact_id: str) -> HTMLResponse:
    """Render an artifact as a human-readable HTML page.

    Work-brief JSON artifacts (``{goal, nodes, assumptions?}``) are
    rendered as a typed, dependency-annotated page; anything else falls
    back to pretty-printed JSON or plain text. This is the endpoint the
    chat TUI deep-links to so users get a readable view instead of raw
    bytes.
    """
    try:
        content, metadata = await get_artifacts().read(artifact_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"artifact {artifact_id} not found"
        ) from exc
    except Exception as exc:
        _log.exception("artifact view failed for %s", artifact_id)
        raise HTTPException(
            status_code=500, detail=f"artifact view failed: {exc}"
        ) from exc
    html = _render_artifact_html(artifact_id, content, dict(metadata))
    return HTMLResponse(content=html)


def _render_artifact_html(
    artifact_id: str, content: bytes, metadata: dict[str, Any]
) -> str:
    """Build a stand-alone HTML page for an artifact.

    Keeps the markup self-contained (inline CSS, no external assets) so
    the view works even when the server is on an isolated network.
    """
    import html as _html
    import json as _json

    key = str(metadata.get("key") or "")
    content_type = str(metadata.get("content_type") or "")
    summary = str(metadata.get("summary") or "")
    created_at = str(metadata.get("created_at") or "")

    body_html = ""
    try:
        parsed = _json.loads(content.decode("utf-8"))
    except Exception:
        parsed = None

    if isinstance(parsed, dict) and "nodes" in parsed and "goal" in parsed:
        body_html = _render_work_brief_html(parsed)
    elif parsed is not None:
        body_html = (
            '<section class="card"><pre class="json">'
            f"{_html.escape(_json.dumps(parsed, indent=2, default=str))}"
            "</pre></section>"
        )
    else:
        try:
            text = content.decode("utf-8")
            body_html = (
                f'<section class="card"><pre>{_html.escape(text)}</pre></section>'
            )
        except Exception:
            body_html = (
                f'<section class="card"><p class="muted">Binary artifact '
                f"({len(content)} bytes, {_html.escape(content_type)}).</p>"
                "</section>"
            )

    meta_rows: list[str] = []
    for label, value in (
        ("key", key),
        ("content_type", content_type),
        ("created_at", created_at),
        ("summary", summary),
    ):
        if value:
            meta_rows.append(
                f"<div><span class='k'>{_html.escape(label)}</span>"
                f"<span class='v'>{_html.escape(value)}</span></div>"
            )

    return _ARTIFACT_HTML_TEMPLATE.format(
        artifact_id=_html.escape(artifact_id),
        title=_html.escape(key or artifact_id),
        meta="".join(meta_rows),
        body=body_html,
        raw_url=f"/api/v1/artifacts/{_html.escape(artifact_id)}",
    )


def _render_work_brief_html(brief: dict[str, Any]) -> str:
    """Render a WorkBrief dict (goal + nodes + assumptions) as HTML cards."""
    import html as _html

    goal = str(brief.get("goal") or "(no goal)")
    nodes = brief.get("nodes") or []
    assumptions = brief.get("assumptions") or []

    node_cards: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        agent_id = str(node.get("agent_id") or "")
        command = str(node.get("command") or "")
        task = str(node.get("task") or "")
        deps = node.get("depends_on") or []
        dep_html = (
            "<div class='deps'>← "
            + ", ".join(f"<code>{_html.escape(str(d))}</code>" for d in deps)
            + "</div>"
            if deps
            else "<div class='deps muted'>no dependencies · root step</div>"
        )
        node_cards.append(
            "<article class='node'>"
            f"<header><code class='node-id'>{_html.escape(node_id)}</code>"
            f"<span class='badge'>{_html.escape(agent_id)}/"
            f"{_html.escape(command)}</span></header>"
            f"{dep_html}"
            f"<p class='task'>{_html.escape(task)}</p>"
            "</article>"
        )

    assumption_list = ""
    if assumptions:
        items = "".join(f"<li>{_html.escape(str(a))}</li>" for a in assumptions)
        assumption_list = (
            "<section class='card'><h2>Assumptions</h2>"
            f"<ul class='assumptions'>{items}</ul></section>"
        )

    return (
        "<section class='card'><h2>Goal</h2>"
        f"<p class='goal'>{_html.escape(goal)}</p></section>"
        f"<section class='card'><h2>Steps ({len(node_cards)})</h2>"
        f"<div class='nodes'>{''.join(node_cards)}</div></section>"
        f"{assumption_list}"
    )


_ARTIFACT_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — monet artifact</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b0f17;
      --panel: #111827;
      --border: #1f2937;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --accent: #a855f7;
      --link: #3b82f6;
    }}
    html, body {{
      margin: 0;
      padding: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
      line-height: 1.5;
    }}
    main {{
      max-width: 960px;
      margin: 0 auto;
      padding: 2rem 1.25rem 4rem;
    }}
    h1 {{
      font-size: 1.5rem;
      margin: 0 0 0.25rem;
      font-weight: 600;
    }}
    h2 {{
      font-size: 0.9rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin: 0 0 0.75rem;
    }}
    .id {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      color: var(--muted);
      font-size: 0.85rem;
      word-break: break-all;
    }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 0.25rem 1rem;
      margin: 1rem 0 1.5rem;
      font-size: 0.85rem;
    }}
    .meta .k {{
      color: var(--muted);
      margin-right: 0.5rem;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem 1.25rem;
      margin: 1rem 0;
    }}
    .goal {{
      font-size: 1.05rem;
      margin: 0;
    }}
    .nodes {{
      display: grid;
      gap: 0.75rem;
    }}
    .node {{
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.75rem 1rem;
    }}
    .node header {{
      display: flex;
      align-items: center;
      gap: 0.6rem;
      margin-bottom: 0.35rem;
    }}
    .node-id {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      color: var(--accent);
    }}
    .badge {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.75rem;
      padding: 0.1rem 0.45rem;
      background: #1e293b;
      border: 1px solid var(--border);
      border-radius: 4px;
      color: var(--muted);
    }}
    .deps {{
      font-size: 0.8rem;
      color: var(--muted);
      margin-bottom: 0.35rem;
    }}
    .deps code {{
      color: var(--accent);
      background: transparent;
      padding: 0;
    }}
    .task {{
      margin: 0;
      color: var(--text);
    }}
    .assumptions {{
      margin: 0;
      padding-left: 1.25rem;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.85rem;
    }}
    .json {{ color: var(--text); }}
    .muted {{ color: var(--muted); }}
    a {{ color: var(--link); }}
    .raw-link {{
      display: inline-block;
      margin-top: 1rem;
      font-size: 0.8rem;
      color: var(--muted);
    }}
    .raw-link a {{ color: var(--muted); }}
    .raw-link a:hover {{ color: var(--link); }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <div class="id">{artifact_id}</div>
    <div class="meta">{meta}</div>
    {body}
    <p class="raw-link">
      <a href="{raw_url}">view raw bytes →</a>
    </p>
  </main>
</body>
</html>
"""


@router.get("/health")
async def health(
    deployments: Deployments,
    queue: Queue,
    response: Response,
) -> HealthResponse:
    """Health check. No authentication required.

    On a Redis-backed queue, PING is required to return 200 — a Redis
    outage must surface as 503 here so load balancers stop routing to
    broken replicas instead of returning a false-healthy 200.
    """
    active = await deployments.get_active()
    worker_count = len(active)
    queued = getattr(queue, "pending_count", 0)
    redis_status: str | None = None
    if isinstance(queue, RedisStreamsTaskQueue):
        if await queue.ping():
            redis_status = "ok"
        else:
            redis_status = "down"
            response.status_code = 503
            return HealthResponse(
                status="degraded",
                workers=worker_count,
                queued=queued,
                redis=redis_status,
            )
    return HealthResponse(
        status="ok", workers=worker_count, queued=queued, redis=redis_status
    )
