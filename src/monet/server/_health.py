"""Health check endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, Any]:
    """Health check."""
    return {"status": "ok"}
