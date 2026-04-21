"""REST API routes for the monet orchestration server."""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from monet import get_artifacts
from monet._ports import MAX_INLINE_PAYLOAD_BYTES
from monet.queue import ProgressStore, TaskQueue
from monet.server._auth import require_api_key, require_task_auth
from monet.server._capabilities import Capability, CapabilityIndex
from monet.server._deployment import DeploymentStore
from monet.types import AgentResult, Signal, build_artifact_pointer

_log = logging.getLogger("monet.server.routes")

__all__ = ["router"]


#: Max characters of a node's ``task`` rendered inside a DAG box.
#: Longer strings are truncated with an ellipsis so node boxes stay
#: scannable — the full task remains in the raw JSON and in the work
#: brief artifact itself.
_DAG_TASK_CHAR_BUDGET = 160

#: Hard cap on artifacts returned by ``GET /api/v1/artifacts``.
ARTIFACT_LIST_MAX = 500


# -- Dependency injection helpers ------------------------------------------


def get_queue(request: Request) -> TaskQueue:
    """Retrieve the task queue from application state."""
    return request.app.state.queue  # type: ignore[no-any-return]


def get_deployments(request: Request) -> DeploymentStore:
    """Retrieve the deployment store from application state."""
    return request.app.state.deployments  # type: ignore[no-any-return]


def get_capability_index(request: Request) -> CapabilityIndex:
    """Retrieve the capability index from application state."""
    return request.app.state.capability_index  # type: ignore[no-any-return]


# Type aliases for annotated dependencies
Queue = Annotated[TaskQueue, Depends(get_queue)]
Deployments = Annotated[DeploymentStore, Depends(get_deployments)]
CapIndex = Annotated[CapabilityIndex, Depends(get_capability_index)]


# -- Request / Response schemas --------------------------------------------


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
    version: str = ""
    queue_backend: str = ""
    uptime_seconds: float = 0.0


# -- Router ----------------------------------------------------------------


router = APIRouter(prefix="/api/v1")


class WorkerHeartbeatBody(BaseModel):
    """Body for unified ``POST /api/v1/workers/{worker_id}/heartbeat``."""

    pool: str
    capabilities: list[Capability]


@router.post(
    "/workers/{worker_id}/heartbeat",
    dependencies=[Depends(require_api_key)],
)
async def worker_heartbeat(
    worker_id: str,
    body: WorkerHeartbeatBody,
    deployments: Deployments,
    cap_index: CapIndex,
) -> dict[str, object]:
    """Unified registration + liveness ping for a worker.

    First call from an unknown ``worker_id`` registers; subsequent calls
    reconcile the capability set. The :class:`CapabilityIndex` is the
    authoritative view; the deployment store tracks liveness for the
    stale sweeper.
    """
    cap_index.upsert_worker(worker_id, body.pool, body.capabilities)
    cap_dicts = [
        {
            "agent_id": c.agent_id,
            "command": c.command,
            "description": c.description,
            "pool": c.pool,
        }
        for c in body.capabilities
    ]

    is_new = not await deployments.worker_exists(worker_id)
    if is_new:
        deployment_id = await deployments.create(body.pool, cap_dicts)
        await deployments.register_worker(deployment_id, worker_id)
    else:
        await deployments.heartbeat(worker_id)
        await deployments.update_capabilities(worker_id, cap_dicts)

    _log.info(
        "worker.heartbeat worker=%s pool=%s caps=%d new=%s",
        worker_id,
        body.pool,
        len(body.capabilities),
        is_new,
    )
    return {
        "worker_id": worker_id,
        "known_capabilities": len(body.capabilities),
        "registered": is_new,
    }


@router.post(
    "/pools/{pool}/claim",
    dependencies=[Depends(require_api_key)],
)
async def claim_from_pool(
    pool: str,
    body: PoolClaimRequest,
    response: Response,
    queue: Queue,
    cap_index: CapIndex,
) -> dict[str, Any] | None:
    """Claim one task from the pool, server-blocking up to ``block_ms``.

    The server issues ``XREADGROUP ... BLOCK block_ms`` (or the memory
    equivalent) so the worker's HTTP request waits until a task lands
    or the timeout elapses. Returns the task record on success or 204
    No Content on timeout.

    ``body.consumer_id`` must be a ``worker_id`` that is currently
    heartbeating for *pool*; otherwise the claim is rejected with 403
    (fixes cross-pool task poaching in S3 / S5 fleets).
    """
    if not cap_index.worker_for_pool(body.consumer_id, pool):
        raise HTTPException(
            403,
            f"worker {body.consumer_id!r} is not heartbeating for pool {pool!r}",
        )
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
    "/runs/{run_id}/progress",
    dependencies=[Depends(require_api_key)],
)
async def get_run_progress(
    run_id: str,
    queue: Queue,
    count: int = Query(default=1000, ge=1, le=10000),
) -> dict[str, Any]:
    """Retrieve persisted progress events for a run.

    Returns 501 when the queue backend does not support progress history.
    """
    if not isinstance(queue, ProgressStore):
        raise HTTPException(501, "Backend does not support progress history")
    events = await queue.get_progress_history(run_id, count=count)
    return {"run_id": run_id, "events": events}


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


@router.get("/agents", dependencies=[Depends(require_api_key)])
async def list_agents(cap_index: CapIndex) -> list[dict[str, Any]]:
    """List every capability advertised by a heartbeating worker.

    Returns one entry per ``(agent_id, command)`` pair with its pool,
    description, and the set of worker ids currently serving it. Used by
    ``MonetClient.list_capabilities`` so clients can discover
    user-defined agents at runtime.
    """
    return cap_index.capabilities()


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
    from monet.orchestration import invoke_agent

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


class ArtifactListItem(BaseModel):
    """Summary of one artifact returned by the list endpoint."""

    artifact_id: str
    key: str
    content_type: str
    content_length: int
    summary: str
    created_at: str
    agent_id: str | None = None
    run_id: str | None = None
    thread_id: str | None = None


class ArtifactListResponse(BaseModel):
    """Paginated artifact list response."""

    artifacts: list[ArtifactListItem]
    next_cursor: str | None = None


@router.get("/artifacts/counts", dependencies=[Depends(require_api_key)])
async def count_artifacts_per_thread(
    thread_ids: str = Query(..., description="Comma-separated thread IDs"),
) -> dict[str, int]:
    """Return artifact counts grouped by thread_id in one query."""
    ids = [t.strip() for t in thread_ids.split(",") if t.strip()]
    if not ids:
        return {}
    store = get_artifacts()
    try:
        counter = getattr(store, "count_per_thread", None)
        if counter is None:
            return {}
        result: dict[str, int] = await counter(ids)
        return result
    except Exception as exc:
        _log.exception("artifact count failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/artifacts", dependencies=[Depends(require_api_key)])
async def list_artifacts(
    thread_id: str = Query(..., description="Required: filter to this thread"),
    limit: int = Query(default=50, ge=1, le=ARTIFACT_LIST_MAX),
    cursor: str | None = Query(default=None, description="ISO 8601 created_at cursor"),
) -> ArtifactListResponse:
    """List recent artifacts for a thread, newest-first.

    ``thread_id`` is required — listing across all threads is not supported
    in keyless dev mode to avoid cross-tenant data exposure.

    ``cursor`` is an opaque ISO 8601 timestamp from ``next_cursor`` of the
    previous response. Pass it to fetch the next page.
    """
    try:
        import asyncio as _asyncio

        rows = await _asyncio.wait_for(
            get_artifacts().query_recent(
                thread_id=thread_id,
                since=cursor,
                limit=limit + 1,
            ),
            timeout=10.0,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="artifact store timeout") from exc
    except Exception as exc:
        _log.exception("artifact list failed for thread %s", thread_id)
        raise HTTPException(
            status_code=500, detail=f"artifact list failed: {exc}"
        ) from exc

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor: str | None = None
    if has_more and page:
        next_cursor = page[-1].get("created_at") or None

    items = [
        ArtifactListItem(
            artifact_id=r["artifact_id"],
            key=r["artifact_id"],
            content_type=r.get("content_type", ""),
            content_length=r.get("content_length", 0),
            summary=r.get("summary", ""),
            created_at=r.get("created_at", ""),
            agent_id=r.get("agent_id"),
            run_id=r.get("run_id"),
            thread_id=r.get("thread_id"),
        )
        for r in page
    ]
    return ArtifactListResponse(artifacts=items, next_cursor=next_cursor)


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
    """Render a WorkBrief dict (goal + nodes + assumptions) as HTML cards.

    The DAG card renders the plan as a top-to-bottom Mermaid graph where
    each node box carries the agent/command (top, accent colour), the
    node id, and the task description. No separate step list — the DAG
    boxes are the canonical step view. Mermaid is loaded from the
    jsDelivr CDN (see ``_ARTIFACT_HTML_TEMPLATE``).
    """
    import html as _html

    goal = str(brief.get("goal") or "(no goal)")
    nodes = brief.get("nodes") or []
    assumptions = brief.get("assumptions") or []

    assumption_list = ""
    if assumptions:
        items = "".join(f"<li>{_html.escape(str(a))}</li>" for a in assumptions)
        assumption_list = (
            "<section class='card'><h2>Assumptions</h2>"
            f"<ul class='assumptions'>{items}</ul></section>"
        )

    dag_card = _render_work_brief_dag(nodes)

    return (
        "<section class='card'><h2>Goal</h2>"
        f"<p class='goal'>{_html.escape(goal)}</p></section>"
        f"{dag_card}"
        f"{assumption_list}"
    )


def _render_work_brief_dag(nodes: list[Any]) -> str:
    """Emit a Mermaid ``graph TB`` card for the plan's node dependencies.

    Each node box contains three stacked labels:

    1. ``agent_id/command`` on top, accent-coloured monospace so the
       agent responsible is visible at a glance.
    2. The plan's ``node_id`` in the middle.
    3. The task description at the bottom, muted, truncated to
       ``_DAG_TASK_CHAR_BUDGET`` chars to keep the DAG scannable.

    Node ids are rewritten to ``n<idx>`` so arbitrary characters in the
    planner's ids (dashes, colons) can never break Mermaid parsing — the
    user-visible label keeps the original id. Direction is top-to-bottom
    so long plans scroll naturally in the browser.
    """
    import html as _html

    diagram_lines: list[str] = ["graph TB"]
    id_map: dict[str, str] = {}

    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id") or f"node{idx}")
        alias = f"n{idx}"
        id_map[nid] = alias
        agent = str(node.get("agent_id") or "")
        command = str(node.get("command") or "")
        badge = f"{agent}/{command}".strip("/")
        task = str(node.get("task") or "")
        if len(task) > _DAG_TASK_CHAR_BUDGET:
            task = task[: _DAG_TASK_CHAR_BUDGET - 1].rstrip() + "…"
        label_id = _mermaid_escape(nid)
        label_badge = _mermaid_escape(badge)
        label_task = _mermaid_escape(task)
        caption_parts: list[str] = []
        if label_badge:
            caption_parts.append(
                f"<span style='color:#a855f7;font-family:monospace;"
                f"font-weight:600'>{label_badge}</span>"
            )
        caption_parts.append(label_id)
        if label_task:
            caption_parts.append(
                f"<span style='color:#9ca3af;font-size:0.85em'>{label_task}</span>"
            )
        caption = "<br/>".join(caption_parts)
        diagram_lines.append(f'  {alias}["{caption}"]')

    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id") or f"node{idx}")
        target = id_map.get(nid)
        if not target:
            continue
        for dep in node.get("depends_on") or []:
            src = id_map.get(str(dep))
            if src:
                diagram_lines.append(f"  {src} --> {target}")

    if len(diagram_lines) == 1:
        return ""

    diagram = "\n".join(diagram_lines)
    # HTML-escape angle brackets and ampersands so the browser's text
    # content shows ``<br/>`` verbatim for Mermaid to parse as a label
    # line break. ``"`` is left raw (Mermaid label quote) — we've already
    # replaced any user ``"`` with the Mermaid ``#quot;`` escape.
    escaped = _html.escape(diagram, quote=False)
    return (
        "<section class='card'><h2>DAG</h2>"
        f'<div class="mermaid">{escaped}</div>'
        "</section>"
    )


def _mermaid_escape(text: str) -> str:
    """Escape characters that would break a Mermaid label."""
    return (
        text.replace("\\", "/").replace('"', "#quot;").replace("<", "").replace(">", "")
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
    .mermaid {{
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.75rem;
      overflow-x: auto;
      text-align: center;
    }}
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
  <script type="module">
    import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
    mermaid.initialize({{
      startOnLoad: false,
      theme: "dark",
      securityLevel: "loose",
      flowchart: {{ htmlLabels: true, curve: "basis" }},
    }});
    mermaid.run({{ querySelector: ".mermaid" }});
  </script>
</body>
</html>
"""


def _monet_version() -> str:
    try:
        from importlib.metadata import version

        return version("monet")
    except Exception:
        return ""


@router.get("/health")
async def health(
    request: Request,
    deployments: Deployments,
    queue: Queue,
    response: Response,
) -> HealthResponse:
    """Health check. No authentication required.

    On a Redis-backed queue, PING is required to return 200 — a Redis
    outage must surface as 503 here so load balancers stop routing to
    broken replicas instead of returning a false-healthy 200.

    Additive fields ``version``, ``queue_backend``, and ``uptime_seconds``
    are present unconditionally so Go clients can perform version-compat
    checks at startup without a separate request.
    """
    active = await deployments.get_active()
    worker_count = len(active)
    queued = getattr(queue, "pending_count", 0)
    start_time: float = getattr(request.app.state, "start_time", 0.0)
    uptime = time.monotonic() - start_time if start_time else 0.0
    backend = queue.backend_name
    version_str = _monet_version()

    healthy = await queue.ping()
    redis_status: str | None = None
    if backend != "memory":
        redis_status = "ok" if healthy else "down"
    if not healthy:
        response.status_code = 503
        return HealthResponse(
            status="degraded",
            workers=worker_count,
            queued=queued,
            redis=redis_status,
            version=version_str,
            queue_backend=backend,
            uptime_seconds=uptime,
        )
    return HealthResponse(
        status="ok",
        workers=worker_count,
        queued=queued,
        redis=redis_status,
        version=version_str,
        queue_backend=backend,
        uptime_seconds=uptime,
    )
