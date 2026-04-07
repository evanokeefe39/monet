"""Content limit enforcement.

Called by the node wrapper after each agent response. If output
exceeds the configured limit, writes full content to the catalogue
and replaces with a pointer and trimmed summary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from monet.catalogue._protocol import CatalogueClient

# Default content limit in characters
DEFAULT_CONTENT_LIMIT = 4000


async def enforce_content_limit(
    entry: dict[str, Any],
    limit: int = DEFAULT_CONTENT_LIMIT,
    catalogue: CatalogueClient | None = None,
) -> dict[str, Any]:
    """Enforce content limit on a state entry.

    If output exceeds limit and a catalogue client is provided,
    writes the full content to the catalogue and replaces the
    output with a trimmed summary + artifact URL.

    Preconditions:
        entry contains 'output' key.
    Postconditions:
        entry['output'] is within limit, or replaced with pointer.
    """
    output = entry.get("output", "")
    if len(output) <= limit:
        return entry

    if catalogue is not None:
        pointer = await catalogue.write(
            content=output.encode(),
            content_type="text/plain",
            summary=output[:200],
            confidence=entry.get("confidence", 0.0),
            completeness="complete",
        )
        entry = dict(entry)
        entry["output"] = output[:limit]
        entry["artifact_url"] = pointer["url"]
        entry["summary"] = output[:200]
    else:
        # No catalogue available — just truncate
        entry = dict(entry)
        entry["output"] = output[:limit]
        entry["summary"] = output[:200]

    return entry
