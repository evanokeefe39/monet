from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph_sdk.client import LangGraphClient

    from monet.client._run_state import _RunStore


@dataclass(frozen=True)
class _ClientCore:
    url: str
    api_key: str | None
    data_url: str
    client: LangGraphClient
    store: _RunStore
    entrypoints: dict[str, Any]
    graph_roles: dict[str, str]
