"""Context resolver — fetch full content from catalogue pointers.

Called by agents that need full upstream output. The orchestration
layer passes only pointers and summaries; this helper resolves them
to full content on the execution side.
"""

from __future__ import annotations

from typing import Any

from monet.core.catalogue import get_catalogue

__all__ = ["resolve_context"]

# MIME types that are safe to decode as UTF-8 text.
_TEXT_PREFIXES: tuple[str, ...] = (
    "text/",
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/javascript",
    "application/x-ndjson",
)


async def resolve_context(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve catalogue pointers in context entries to full content.

    For each entry with ``artifacts``, fetches text content from the
    catalogue and adds it as ``content``. Binary artifacts get a
    metadata stub. Entries without artifacts are passed through unchanged.

    Args:
        entries: Context entries from ``pending_context``, each with
            ``type``, ``agent_id``, ``command``, ``summary``, and
            optionally ``artifacts`` (list of pointer dicts).

    Returns:
        Enriched entries with ``content`` field populated from catalogue.
    """
    catalogue = get_catalogue()
    resolved: list[dict[str, Any]] = []

    for entry in entries:
        artifacts = entry.get("artifacts") or []
        if not artifacts:
            resolved.append(entry)
            continue

        text_blocks: list[str] = []
        binary_stubs: list[str] = []

        for art in artifacts:
            art_id = art.get("artifact_id") or art.get("id")
            if not art_id:
                continue
            try:
                raw, meta = await catalogue.read(art_id)
            except (KeyError, ValueError, FileNotFoundError):
                continue
            content_type = (meta.get("content_type") or "").lower()
            if any(content_type.startswith(p) for p in _TEXT_PREFIXES):
                text_blocks.append(raw.decode("utf-8", errors="replace"))
            else:
                size = meta.get("content_length") or len(raw)
                binary_stubs.append(
                    f"[binary artifact {art_id[:8]} "
                    f"type={content_type or 'unknown'} size={size}b]"
                )

        content = ""
        if text_blocks:
            content = "\n\n---\n\n".join(text_blocks)
        elif binary_stubs:
            content = "\n".join(binary_stubs)

        resolved.append({**entry, "content": content})

    return resolved
