from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .._env import (
    MONET_CHAT_GRAPH,
    MONET_CHAT_RESPOND_MODEL,
    MONET_CHAT_TRIAGE_MODEL,
    MONET_SKIP_SMOKE_TEST,
    ConfigError,
    read_bool,
    read_str,
)
from .._load import read_toml_section

_DEFAULT_CHAT_GRAPH = "monet.orchestration.prebuilt.chat_graph:build_chat_graph"
_DEFAULT_CHAT_RESPOND_MODEL = "groq:llama-3.3-70b-versatile"
_DEFAULT_CHAT_TRIAGE_MODEL = "groq:llama-3.1-8b-instant"


class ChatConfig(BaseModel):
    """Config surface for the chat graph.

    ``graph`` is a dotted ``module.path:factory`` reference that Aegra
    invokes to build the chat ``StateGraph``. The default points at the
    built-in implementation in :mod:`monet.orchestration.prebuilt.chat_graph`;
    users override it in ``monet.toml [chat]`` or via
    ``MONET_CHAT_GRAPH`` to swap in an agentic variant that delegates
    response generation to a ``conversationalist`` agent.

    ``respond_model`` and ``triage_model`` are LangChain-style model
    strings (``provider:name``). The respond model drives the direct
    LLM call in ``respond_node``; the triage model drives the
    structured-output classifier in ``triage_node`` and should be a
    small/fast model so routing stays cheap.
    """

    model_config = ConfigDict(frozen=True)

    graph: str = _DEFAULT_CHAT_GRAPH
    respond_model: str = _DEFAULT_CHAT_RESPOND_MODEL
    triage_model: str = _DEFAULT_CHAT_TRIAGE_MODEL
    skip_smoke_test: bool = True

    @classmethod
    def load(cls) -> ChatConfig:
        section = read_toml_section("chat")
        toml_graph = section.get("graph") if isinstance(section, dict) else None
        toml_respond = (
            section.get("respond_model") if isinstance(section, dict) else None
        )
        toml_triage = section.get("triage_model") if isinstance(section, dict) else None
        graph = (
            read_str(MONET_CHAT_GRAPH)
            or (toml_graph if isinstance(toml_graph, str) and toml_graph else None)
            or _DEFAULT_CHAT_GRAPH
        )
        respond_model = (
            read_str(MONET_CHAT_RESPOND_MODEL)
            or (
                toml_respond if isinstance(toml_respond, str) and toml_respond else None
            )
            or _DEFAULT_CHAT_RESPOND_MODEL
        )
        triage_model = (
            read_str(MONET_CHAT_TRIAGE_MODEL)
            or (toml_triage if isinstance(toml_triage, str) and toml_triage else None)
            or _DEFAULT_CHAT_TRIAGE_MODEL
        )
        skip_smoke_test = read_bool(MONET_SKIP_SMOKE_TEST, True) or (
            section.get("skip_smoke_test") if isinstance(section, dict) else True
        )
        return cls(
            graph=graph,
            respond_model=respond_model,
            triage_model=triage_model,
            skip_smoke_test=bool(skip_smoke_test),
        )

    def validate_for_boot(self) -> None:
        """Resolve the ``graph`` dotted path and fail fast if missing.

        ``graph`` must be ``<module.path>:<factory>``. The module must
        import cleanly and the factory attribute must exist. A typo here
        would otherwise surface as a 500 at request time.
        """
        if ":" not in self.graph:
            raise ConfigError(
                MONET_CHAT_GRAPH,
                self.graph,
                "a dotted path of the form 'module.path:factory'",
            )
        module_part, _, factory = self.graph.rpartition(":")
        if not module_part or not factory:
            raise ConfigError(
                MONET_CHAT_GRAPH,
                self.graph,
                "a dotted path of the form 'module.path:factory'",
            )
        try:
            import importlib

            mod = importlib.import_module(module_part)
        except ModuleNotFoundError as exc:
            raise ConfigError(
                MONET_CHAT_GRAPH,
                self.graph,
                f"an importable module (ModuleNotFoundError: {exc})",
            ) from None
        if not hasattr(mod, factory):
            raise ConfigError(
                MONET_CHAT_GRAPH,
                self.graph,
                f"a callable named '{factory}' on module '{module_part}'",
            )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "graph": self.graph,
            "respond_model": self.respond_model,
            "triage_model": self.triage_model,
        }
