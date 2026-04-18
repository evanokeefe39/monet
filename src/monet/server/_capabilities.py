"""Server-side capability index.

Populated only by worker heartbeats. The server never imports
``@agent`` decorators — a capability exists for the server iff at
least one heartbeating worker advertises it.

``Capability`` validates the wire format at the boundary: empty
``agent_id``/``command`` are rejected, ``pool`` is constrained to
``[a-z0-9_-]+``. Multiple workers may serve the same
``(agent_id, command)`` pair (horizontal scale-out); the index keeps
the full set of worker ids per capability so dropping one worker
leaves the capability intact when others still serve it.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

__all__ = ["RESERVED_SLASH", "Capability", "CapabilityIndex"]


#: Slash commands always present regardless of heartbeat state.
#: ``/plan`` is chat's hand-off into the planning pipeline.
RESERVED_SLASH: tuple[str, ...] = ("/plan",)

_NAME_RE = re.compile(r"^[a-z0-9_\-]+$")


def _check_name(value: str, field_name: str) -> str:
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > 64:
        raise ValueError(f"{field_name} must be <= 64 chars")
    if not _NAME_RE.match(value):
        raise ValueError(f"{field_name} must match {_NAME_RE.pattern!r}")
    return value


class Capability(BaseModel):
    """Wire-validated capability advertised by a worker heartbeat."""

    agent_id: str
    command: str
    pool: str
    description: str = Field(default="", max_length=512)

    @field_validator("agent_id")
    @classmethod
    def _agent_id(cls, v: str) -> str:
        return _check_name(v, "agent_id")

    @field_validator("command")
    @classmethod
    def _command(cls, v: str) -> str:
        return _check_name(v, "command")

    @field_validator("pool")
    @classmethod
    def _pool(cls, v: str) -> str:
        return _check_name(v, "pool")


@dataclass
class _Entry:
    pool: str
    description: str
    worker_ids: set[str] = field(default_factory=set)


class CapabilityIndex:
    """Thread-safe index of live capabilities, keyed by worker.

    ``upsert_worker`` replaces the full capability set for the given
    ``worker_id`` — capabilities previously advertised by that worker
    but absent in the new list are dropped (possibly pruned if no
    other worker serves them).
    """

    def __init__(self) -> None:
        self._caps: dict[tuple[str, str], _Entry] = {}
        self._workers: dict[str, set[tuple[str, str]]] = {}
        self._worker_pools: dict[str, str] = {}
        self._lock = threading.Lock()

    def upsert_worker(
        self,
        worker_id: str,
        pool: str,
        capabilities: list[Capability],
    ) -> None:
        new_keys = {(c.agent_id, c.command) for c in capabilities}
        with self._lock:
            old_keys = self._workers.get(worker_id, set())
            for key in old_keys - new_keys:
                entry = self._caps.get(key)
                if entry is None:
                    continue
                entry.worker_ids.discard(worker_id)
                if not entry.worker_ids:
                    del self._caps[key]
            for cap in capabilities:
                key = (cap.agent_id, cap.command)
                entry = self._caps.get(key)
                if entry is None:
                    entry = _Entry(pool=cap.pool, description=cap.description)
                    self._caps[key] = entry
                else:
                    entry.pool = cap.pool
                    entry.description = cap.description
                entry.worker_ids.add(worker_id)
            self._workers[worker_id] = new_keys
            self._worker_pools[worker_id] = pool

    def drop_worker(self, worker_id: str) -> list[tuple[str, str]]:
        """Remove *worker_id*; return list of capabilities pruned entirely."""
        pruned: list[tuple[str, str]] = []
        with self._lock:
            keys = self._workers.pop(worker_id, set())
            self._worker_pools.pop(worker_id, None)
            for key in keys:
                entry = self._caps.get(key)
                if entry is None:
                    continue
                entry.worker_ids.discard(worker_id)
                if not entry.worker_ids:
                    del self._caps[key]
                    pruned.append(key)
        return pruned

    def get_pool(self, agent_id: str, command: str) -> str | None:
        with self._lock:
            entry = self._caps.get((agent_id, command))
            return entry.pool if entry else None

    def is_available(self, agent_id: str, command: str) -> bool:
        with self._lock:
            return (agent_id, command) in self._caps

    def worker_for_pool(self, worker_id: str, pool: str) -> bool:
        """True when *worker_id* is currently heartbeating for *pool*."""
        with self._lock:
            return self._worker_pools.get(worker_id) == pool

    def capabilities(self) -> list[dict[str, object]]:
        """Snapshot as a list of dicts — stable wire shape for GET /agents."""
        with self._lock:
            return [
                {
                    "agent_id": agent_id,
                    "command": command,
                    "pool": entry.pool,
                    "description": entry.description,
                    "worker_ids": sorted(entry.worker_ids),
                }
                for (agent_id, command), entry in sorted(self._caps.items())
            ]

    def slash_commands(self) -> list[str]:
        out: list[str] = list(RESERVED_SLASH)
        seen: set[str] = set(out)
        with self._lock:
            keys = sorted(self._caps.keys())
        for agent_id, command in keys:
            cmd = f"/{agent_id}:{command}"
            if cmd not in seen:
                out.append(cmd)
                seen.add(cmd)
        return out

    def clear(self) -> None:
        with self._lock:
            self._caps.clear()
            self._workers.clear()
            self._worker_pools.clear()
