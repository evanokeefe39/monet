"""Reference agents — importing this module registers all five via @agent.

The decorator IS the registration. No factory functions, no startup ceremony.
Models are constructed lazily on first invocation via init_chat_model() so
import succeeds without provider packages or API keys.

:func:`register_reference_agents` is a tiny re-registration helper for
callers (e.g. the monolith in-process worker lifespan) that need the
reference agents present in ``default_registry`` regardless of prior
test-scope teardown. Each submodule exposes its decorated handlers as
module-level objects; this function re-inserts them into the registry
under their decorated ``(agent_id, command)`` keys.
"""

from __future__ import annotations

from monet.core.registry import default_registry

from . import planner, publisher, qa, researcher, writer


def register_reference_agents() -> int:
    """Ensure the five reference agents are present in ``default_registry``.

    Idempotent. Returns the number of (re-)registered agents. Useful when
    test scopes have rolled back the module-import registrations — a plain
    ``import monet.agents`` is a no-op after the first load (sys.modules)
    so the side-effect does not re-run.
    """
    count = 0
    for mod in (planner, publisher, qa, researcher, writer):
        for attr in vars(mod).values():
            agent_id = getattr(attr, "_agent_id", None)
            command = getattr(attr, "_command", None)
            if agent_id and command:
                default_registry.register(agent_id, command, attr)
                count += 1
    return count
