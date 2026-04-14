"""AgentManifest handle — worker-side access to agent registry.

get_agent_manifest() is a core SDK getter alongside get_catalogue()
and get_run_context(). Workers configure the backend at startup.

In monolith mode, server bootstrap calls configure_agent_manifest()
and the in-process worker inherits it. In distributed mode, each
worker calls configure_agent_manifest() independently in its own
startup sequence before entering the claim loop.

The orchestrator does not call get_agent_manifest() for correctness.
Pool routing in invoke_agent is a convenience lookup only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.core.manifest import AgentCapability, AgentManifest


_backend: AgentManifest | None = None


def _set_agent_manifest_backend(manifest: AgentManifest | None) -> None:
    """Internal — call configure_agent_manifest() from monet.agent_manifest."""
    global _backend
    _backend = manifest


class AgentManifestHandle:
    """Returned by get_agent_manifest(). Read-only access to the manifest.

    Reads the module-level backend on every call so configure_agent_manifest()
    takes effect immediately for the existing singleton.
    """

    def list_agents(self) -> list[AgentCapability]:
        """Return all declared agent capabilities."""
        if _backend is None:
            msg = (
                "get_agent_manifest() requires a backend. "
                "Call configure_agent_manifest() at startup."
            )
            raise RuntimeError(msg)
        return _backend.capabilities()

    def is_available(self, agent_id: str, command: str) -> bool:
        """Check if a capability has been declared.

        Returns False if no backend is configured — read-side availability
        checks are best-effort and must not raise in test contexts.
        """
        return _backend.is_available(agent_id, command) if _backend else False

    def get_pool(self, agent_id: str, command: str) -> str | None:
        """Return the pool for a declared capability, or None.

        Returns None if no backend is configured.
        """
        return _backend.get_pool(agent_id, command) if _backend else None

    def is_configured(self) -> bool:
        """Return True if a backend has been configured."""
        return _backend is not None


_handle_instance = AgentManifestHandle()


def get_agent_manifest() -> AgentManifestHandle:
    """Return the agent manifest handle.

    One of the core SDK getters alongside get_catalogue() and
    get_run_context(). Works anywhere the worker runtime is configured.
    """
    return _handle_instance
