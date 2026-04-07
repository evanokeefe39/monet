"""Reference agents — importing this module registers all five via @agent.

The decorator IS the registration. No factory functions, no startup ceremony.
Models are constructed lazily on first invocation via init_chat_model() so
import succeeds without provider packages or API keys.
"""

from . import planner, publisher, qa, researcher, writer  # noqa: F401
