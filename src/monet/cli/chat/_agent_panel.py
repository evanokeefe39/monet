"""AgentPanel: live parallel-agent progress tree."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Tree

if TYPE_CHECKING:
    from textual.widgets.tree import TreeNode


class AgentPanel(Tree[str]):
    """Collapsible tree showing per-agent run progress.

    Preconditions: mounted inside a running Textual App.
    Postconditions: each add_agent call creates exactly one root child node;
    update_agent appends a leaf to that node; complete_agent collapses it.
    """

    DEFAULT_CSS = """
    AgentPanel {
        height: auto;
        max-height: 12;
        border: round $primary;
        padding: 0 1;
        display: none;
    }
    AgentPanel.active {
        display: block;
    }
    """

    def __init__(self, *, id: str = "agent-panel") -> None:
        super().__init__("agents", id=id)
        self._agents: dict[str, TreeNode[str]] = {}

    def add_agent(self, agent_key: str) -> None:
        """Create a tree node for agent_key if not already present."""
        if agent_key not in self._agents:
            node = self.root.add(agent_key, expand=True)
            self._agents[agent_key] = node
            self.add_class("active")

    def update_agent(self, agent_key: str, status: str) -> None:
        """Append a status leaf under agent_key's node."""
        if agent_key not in self._agents:
            self.add_agent(agent_key)
        self._agents[agent_key].add_leaf(status)

    def complete_agent(self, agent_key: str) -> None:
        """Collapse the agent's node to indicate completion."""
        if agent_key in self._agents:
            self._agents[agent_key].collapse()

    def clear_agents(self) -> None:
        """Clear all agent nodes and hide the panel."""
        for node in list(self._agents.values()):
            node.remove()
        self._agents.clear()
        self.remove_class("active")
