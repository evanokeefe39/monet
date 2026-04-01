"""Catalogue HTTP routes.

POST /artifacts — write an artifact
GET /artifacts/{artifact_id} — read artifact content
GET /artifacts/{artifact_id}/meta — read artifact metadata
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, Response

if TYPE_CHECKING:
    from monet.catalogue._protocol import CatalogueClient

router = APIRouter()

_catalogue_service: CatalogueClient | None = None


def set_catalogue_service(service: CatalogueClient) -> None:
    """Inject the catalogue service at startup."""
    global _catalogue_service
    _catalogue_service = service


def _require_service() -> CatalogueClient:
    if _catalogue_service is None:
        raise HTTPException(status_code=501, detail="Catalogue service not configured")
    return _catalogue_service


@router.post("")
async def write_artifact(request: Request) -> dict[str, Any]:
    """Write an artifact to the catalogue.

    Expects multipart form with 'content' file and 'metadata' JSON field.
    For simplicity, accepts raw bytes body with metadata in headers.
    """
    service = _require_service()

    body = await request.body()
    content_type = request.headers.get("content-type", "application/octet-stream")
    summary = request.headers.get("x-monet-summary", "")
    created_by = request.headers.get("x-monet-created-by", "unknown")
    trace_id = request.headers.get("x-monet-trace-id", "")
    run_id = request.headers.get("x-monet-run-id", "")

    from monet.catalogue._metadata import ArtifactMetadata

    metadata = ArtifactMetadata(
        content_type=content_type,
        summary=summary,
        created_by=created_by,
        trace_id=trace_id,
        run_id=run_id,
    )
    pointer = service.write(body, metadata)
    return {
        "artifact_id": pointer.artifact_id,
        "url": pointer.url,
    }


@router.get("/{artifact_id}")
async def read_artifact(artifact_id: str) -> Response:
    """Read artifact content."""
    service = _require_service()
    try:
        content, metadata = service.read(artifact_id)
        return Response(
            content=content,
            media_type=metadata.content_type,
        )
    except KeyError:
        raise HTTPException(  # noqa: B904
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found",
        )


@router.get("/{artifact_id}/meta")
async def read_artifact_meta(artifact_id: str) -> dict[str, Any]:
    """Read artifact metadata."""
    service = _require_service()
    try:
        _, metadata = service.read(artifact_id)
        result: dict[str, Any] = metadata.model_dump()
        return result
    except KeyError:
        raise HTTPException(  # noqa: B904
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found",
        )
