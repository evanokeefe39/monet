"""Public configure_agent_manifest() — sets the backend for get_agent_manifest().

Monolith: server bootstrap calls this with ``default_manifest``.
The in-process worker inherits the configured backend.

Distributed: each worker calls ``configure_agent_manifest()``
independently in its own startup sequence before entering the
claim loop. The server does not configure the manifest on behalf
of remote workers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from monet.core.agent_manifest import _set_agent_manifest_backend

if TYPE_CHECKING:
    from monet.core.manifest import AgentManifest


__all__ = ["configure_agent_manifest"]


def configure_agent_manifest(manifest: AgentManifest | None) -> None:
    """Configure the agent manifest backend.

    Pass None to reset — useful in tests.
    """
    _set_agent_manifest_backend(manifest)
