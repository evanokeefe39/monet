"""Stub implementations for SDK functions not yet wired to real services.

write_artifact() raises NotImplementedError until the catalogue is built.
emit_progress() is a no-op until LangGraph's get_stream_writer() is available.
"""

from __future__ import annotations

from typing import Any


def write_artifact(
    content: bytes,
    content_type: str,
    summary: str = "",
    confidence: float = 0.0,
    completeness: str = "complete",
    sensitivity_label: str = "internal",
    **kwargs: Any,
) -> Any:
    """Write an artifact to the catalogue.

    Preconditions:
        Must be called inside a decorated agent function.
    Postconditions:
        Returns an ArtifactPointer with the artifact ID and URL.

    Currently raises NotImplementedError — will be wired to the
    CatalogueClient in Phase 2.
    """
    raise NotImplementedError(
        "write_artifact() is not yet connected to a catalogue. "
        "The catalogue will be built in Phase 2."
    )


def emit_progress(data: dict[str, Any]) -> None:
    """Emit a progress event for intra-node streaming.

    No-op outside the LangGraph execution context. Will be wired
    to LangGraph's get_stream_writer() when orchestration is built.
    """
