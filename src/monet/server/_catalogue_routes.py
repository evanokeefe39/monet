"""Catalogue HTTP routes.

POST /artifacts — write an artifact
GET /artifacts/{artifact_id} — read artifact content
GET /artifacts/{artifact_id}/meta — read artifact metadata
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response

router = APIRouter()

# The catalogue service is injected at app startup.
# For now, routes are defined but return 501 until wired.
_catalogue_service: Any = None


def set_catalogue_service(service: Any) -> None:
    """Inject the catalogue service at startup."""
    global _catalogue_service
    _catalogue_service = service


@router.post("")
async def write_artifact() -> dict[str, str]:
    """Write an artifact to the catalogue."""
    if _catalogue_service is None:
        raise HTTPException(status_code=501, detail="Catalogue service not configured")
    # Implementation will be wired when catalogue HTTP client is built
    raise HTTPException(status_code=501, detail="Not yet implemented")


@router.get("/{artifact_id}")
async def read_artifact(artifact_id: str) -> Response:
    """Read artifact content."""
    if _catalogue_service is None:
        raise HTTPException(status_code=501, detail="Catalogue service not configured")
    try:
        content, metadata = _catalogue_service.read(artifact_id)
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
    if _catalogue_service is None:
        raise HTTPException(status_code=501, detail="Catalogue service not configured")
    try:
        _, metadata = _catalogue_service.read(artifact_id)
        result: dict[str, Any] = metadata.model_dump()
        return result
    except KeyError:
        raise HTTPException(  # noqa: B904
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found",
        )
