"""Gateway route handlers and GatewayContext dependency.

All route handlers close over a GatewayContext instance. Authentication
is enforced on every route except GET /health.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from monet.worker.gateway._auth import validate_token

if TYPE_CHECKING:
    from monet.artifacts._protocol import ArtifactClient
    from monet.progress._protocol import ProgressWriter

__all__ = ["GatewayContext", "mount_routes"]


@dataclass
class GatewayContext:
    """Shared state injected into all gateway route handlers.

    Precondition: artifact_client and progress_writer are already
    initialised and ready to accept calls.
    """

    artifact_client: ArtifactClient
    progress_writer: ProgressWriter
    signing_key: str
    # task_id+key -> artifact_id index for GET /artifacts/{task_id}/{key}
    _artifacts: dict[tuple[str, str], str] = field(default_factory=dict, init=False)
    # task_id -> list of signal dicts
    _signals: dict[str, list[dict[str, Any]]] = field(default_factory=dict, init=False)


def _auth_claims(request: Request, task_id: str, ctx: GatewayContext) -> dict[str, Any]:
    """Extract and validate bearer token, checking task_id matches URL.

    Args:
        request: Incoming FastAPI request.
        task_id: task_id from the URL path segment.
        ctx: Gateway context holding the signing key.

    Returns:
        Decoded JWT claims dict.

    Raises:
        HTTPException(401): Missing/invalid/expired token or task_id mismatch.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header[len("Bearer ") :]
    try:
        claims = validate_token(token, ctx.signing_key)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if claims.get("task_id") != task_id:
        raise HTTPException(
            status_code=401,
            detail="Token task_id does not match URL task_id",
        )
    return claims


def mount_routes(app: FastAPI, ctx: GatewayContext) -> None:
    """Mount all gateway routes onto *app* with *ctx* captured via closure.

    Args:
        app: FastAPI application instance.
        ctx: Gateway context — captured by all handlers.
    """

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.post("/artifacts/{task_id}")
    async def write_artifact(
        task_id: str,
        request: Request,
        file: UploadFile,
        key: str = Form(default="_result"),
    ) -> JSONResponse:
        _auth_claims(request, task_id, ctx)
        data = await file.read()
        pointer = await ctx.artifact_client.write(
            content=data, key=key, task_id=task_id
        )
        ctx._artifacts[(task_id, key)] = pointer["artifact_id"]
        return JSONResponse(
            {"artifact_id": pointer["artifact_id"], "key": pointer.get("key", key)}
        )

    @app.get("/artifacts/{task_id}/{key}")
    async def read_artifact(
        task_id: str,
        key: str,
        request: Request,
    ) -> Response:
        _auth_claims(request, task_id, ctx)
        artifact_id = ctx._artifacts.get((task_id, key))
        if artifact_id is None:
            raise HTTPException(
                status_code=404,
                detail=f"Artifact not found: task_id={task_id!r} key={key!r}",
            )
        try:
            content, _ = await ctx.artifact_client.read(artifact_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(content=content, media_type="application/octet-stream")

    @app.post("/progress/{task_id}")
    async def write_progress(
        task_id: str,
        request: Request,
    ) -> JSONResponse:
        claims = _auth_claims(request, task_id, ctx)
        run_id: str = claims.get("run_id", "")
        body = await request.json()
        event_id = await ctx.progress_writer.record(run_id, body)
        return JSONResponse({"event_id": event_id})

    @app.post("/signals/{task_id}")
    async def accumulate_signal(
        task_id: str,
        request: Request,
    ) -> JSONResponse:
        _auth_claims(request, task_id, ctx)
        body: dict[str, Any] = await request.json()
        bucket = ctx._signals.setdefault(task_id, [])
        bucket.append(body)
        return JSONResponse({"count": len(bucket)})

    @app.get("/signals/{task_id}")
    async def get_signals(
        task_id: str,
        request: Request,
    ) -> JSONResponse:
        _auth_claims(request, task_id, ctx)
        return JSONResponse({"signals": ctx._signals.get(task_id, [])})
