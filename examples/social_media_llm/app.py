"""Local process configuration for the social_media_llm example.

Owns three concerns the CLI process needs even though the langgraph
server does the heavy lifting:

  1. Loading ``.env`` so the CLI sees the same API keys as the server
     process started with ``langgraph dev``.
  2. Wiring a local catalogue handle so ``display.print_wave_results``
     can read artifact bytes back through ``monet.get_catalogue()``.
     The server writes through the same ``MONET_CATALOGUE_DIR``.
  3. Cheap startup checks: required env vars present and the
     LangGraph server reachable.

This module does **not** import ``monet.agents``. The reference agents
are loaded inside the server process via ``langgraph.json``'s
``dependencies: ["."]`` entry. The CLI process never invokes them
locally.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from monet.catalogue import catalogue_from_env, configure_catalogue

if TYPE_CHECKING:
    from langgraph_sdk.client import LangGraphClient

#: Required environment keys for the reference agent stack.
REQUIRED_KEYS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "TAVILY_API_KEY",
)


def configure_app() -> None:
    """Load env vars and wire the local catalogue handle.

    Idempotent. Safe to call from tests as well as the CLI entry point.
    """
    load_dotenv()
    default_root = Path(__file__).resolve().parent / ".catalogue"
    configure_catalogue(catalogue_from_env(default_root=default_root))


def check_environment() -> list[str]:
    """Return the list of required env keys that are missing or blank."""
    return [k for k in REQUIRED_KEYS if not os.environ.get(k)]


async def check_server(client: LangGraphClient) -> bool:
    """Return True if the LangGraph server is reachable.

    Probes via ``client.assistants.search(limit=1)`` — the cheapest call
    that goes all the way through the server's HTTP stack.
    """
    try:
        await client.assistants.search(limit=1)
    except Exception:
        # Any failure means the server is unreachable from the CLI's POV.
        return False
    return True
