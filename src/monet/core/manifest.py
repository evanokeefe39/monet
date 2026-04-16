"""Agent capability manifest — static declaration of available agents.

The manifest answers "what's available?" for the orchestration layer.
It is separate from the handler registry ("how to execute?") which
lives on the worker side.

Populated by the ``@agent`` decorator at decoration time, or by
explicit ``declare()`` calls for remote agents.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from collections.abc import Generator

__all__ = ["RESERVED_SLASH", "AgentCapability", "AgentManifest", "default_manifest"]


#: Slash commands that are always available regardless of registered agents.
#: ``/plan`` is the top-level hand-off from chat to the planning pipeline.
RESERVED_SLASH: tuple[str, ...] = ("/plan",)


class AgentCapability(TypedDict, total=False):
    """Declared capability of an agent.

    ``worker_id`` is optional — ``None`` for local (in-process) agents,
    set for capabilities registered by remote workers.
    """

    agent_id: str
    command: str
    description: str
    pool: str
    worker_id: str | None


class AgentManifest:
    """Registry of agent capabilities with per-worker tracking.

    Thread-safe via Lock. Populated at decoration/startup time and
    reconciled by worker heartbeats.
    """

    def __init__(self) -> None:
        self._capabilities: dict[tuple[str, str], AgentCapability] = {}
        self._worker_capabilities: dict[str, set[tuple[str, str]]] = {}
        self._lock = threading.Lock()

    def declare(
        self,
        agent_id: str,
        command: str,
        description: str = "",
        pool: str = "local",
        worker_id: str | None = None,
    ) -> None:
        """Declare that an agent capability exists.

        Args:
            agent_id: Agent identifier.
            command: Command name.
            description: Human-readable description.
            pool: Pool assignment.
            worker_id: Worker that provides this capability. ``None``
                for local (in-process) agents.
        """
        key = (agent_id, command)
        with self._lock:
            self._capabilities[key] = AgentCapability(
                agent_id=agent_id,
                command=command,
                description=description,
                pool=pool,
                worker_id=worker_id,
            )
            if worker_id is not None:
                self._worker_capabilities.setdefault(worker_id, set()).add(key)

    def remove(self, agent_id: str, command: str) -> None:
        """Remove a single capability declaration."""
        with self._lock:
            cap = self._capabilities.pop((agent_id, command), None)
            if cap and cap.get("worker_id"):
                wid = cap["worker_id"]
                worker_keys = self._worker_capabilities.get(wid)  # type: ignore[arg-type]
                if worker_keys:
                    worker_keys.discard((agent_id, command))
                    if not worker_keys:
                        del self._worker_capabilities[wid]  # type: ignore[arg-type]

    def remove_by_worker(self, worker_id: str) -> list[AgentCapability]:
        """Remove all capabilities attributed to a specific worker.

        Returns the list of removed capabilities. Capabilities declared
        by other workers or locally are not affected.
        """
        with self._lock:
            keys = self._worker_capabilities.pop(worker_id, set())
            removed: list[AgentCapability] = []
            for key in keys:
                cap = self._capabilities.pop(key, None)
                if cap is not None:
                    removed.append(cap)
            return removed

    def reconcile_worker(
        self,
        worker_id: str,
        capabilities: list[AgentCapability],
    ) -> None:
        """Reconcile a worker's capabilities with the manifest.

        Declares all provided capabilities for this worker. Removes any
        capabilities previously tracked for this worker that are no
        longer in the provided list. Other workers' entries are untouched.

        Args:
            worker_id: The worker providing these capabilities.
            capabilities: Current full capability list from the worker.
        """
        new_keys: set[tuple[str, str]] = set()
        with self._lock:
            # Declare/update all provided capabilities.
            for cap in capabilities:
                key = (cap["agent_id"], cap["command"])
                new_keys.add(key)
                self._capabilities[key] = AgentCapability(
                    agent_id=cap["agent_id"],
                    command=cap["command"],
                    description=cap.get("description", ""),
                    pool=cap.get("pool", "local"),
                    worker_id=worker_id,
                )

            # Remove capabilities this worker previously had but no longer advertises.
            old_keys = self._worker_capabilities.get(worker_id, set())
            stale_keys = old_keys - new_keys
            for key in stale_keys:
                self._capabilities.pop(key, None)

            # Update reverse index.
            if new_keys:
                self._worker_capabilities[worker_id] = new_keys
            else:
                self._worker_capabilities.pop(worker_id, None)

    def get_pool(self, agent_id: str, command: str) -> str | None:
        """Return the pool for a declared capability, or None."""
        with self._lock:
            cap = self._capabilities.get((agent_id, command))
            return cap["pool"] if cap else None

    def is_available(self, agent_id: str, command: str) -> bool:
        """Check if a capability has been declared."""
        with self._lock:
            return (agent_id, command) in self._capabilities

    def capabilities(self) -> list[AgentCapability]:
        """Return all declared capabilities."""
        with self._lock:
            return list(self._capabilities.values())

    def slash_commands(self) -> list[str]:
        """Return the full slash-command vocabulary for this manifest.

        Combines :data:`RESERVED_SLASH` (framework-reserved prefixes like
        ``/plan``) with ``/<agent_id>:<command>`` derived from every
        declared capability. Order: reserved first (in declaration
        order), then capabilities in registration order. Duplicates are
        dropped.
        """
        out: list[str] = list(RESERVED_SLASH)
        seen: set[str] = set(out)
        for cap in self.capabilities():
            cmd = f"/{cap['agent_id']}:{cap['command']}"
            if cmd not in seen:
                out.append(cmd)
                seen.add(cmd)
        return out

    def clear(self) -> None:
        """Remove all declarations."""
        with self._lock:
            self._capabilities.clear()
            self._worker_capabilities.clear()

    @contextlib.contextmanager
    def manifest_scope(self) -> Generator[None]:
        """Context manager that snapshots and restores manifest state.

        Use in tests to isolate capability declarations.
        """
        with self._lock:
            snapshot = dict(self._capabilities)
            worker_snapshot = {k: set(v) for k, v in self._worker_capabilities.items()}
        try:
            yield
        finally:
            with self._lock:
                self._capabilities = snapshot
                self._worker_capabilities = worker_snapshot


default_manifest = AgentManifest()
