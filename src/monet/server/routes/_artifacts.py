"""Artifact management, streaming, and rendering routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from monet import get_artifacts
from monet.server._auth import require_api_key
from monet.server.routes._common import _DAG_TASK_CHAR_BUDGET, ARTIFACT_LIST_MAX

_log = logging.getLogger("monet.server.routes.artifacts")

router = APIRouter()


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
        result: dict[str, int] = await store.count_per_thread(ids)
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
    """List recent artifacts for a thread, newest-first."""
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
            key=str(r.get("key") or r["artifact_id"]),
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
    """Stream the raw bytes of an artifact by id."""
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
    """Render an artifact as a human-readable HTML page."""
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


# -- HTML Rendering Helpers ------------------------------------------------


def _render_artifact_html(
    artifact_id: str, content: bytes, metadata: dict[str, Any]
) -> str:
    """Build a stand-alone HTML page for an artifact."""
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
    """Emit a Mermaid ``graph TB`` card for the plan's node dependencies."""
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
