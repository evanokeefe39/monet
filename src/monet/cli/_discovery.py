"""AST-based discovery of @agent decorated functions.

Scans Python source files for functions decorated with the monet
``@agent`` decorator. No code is executed — discovery is purely
syntactic via the ``ast`` module.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["DiscoveredAgent", "discover_agents"]

# Directories skipped during recursive scanning.
_SKIP_DIRS: frozenset[str] = frozenset({"__pycache__", ".venv", "node_modules"})

# Default values matching the @agent decorator's own defaults.
_DEFAULT_COMMAND = "fast"
_DEFAULT_POOL = "local"


@dataclass(frozen=True)
class DiscoveredAgent:
    """An agent discovered via AST scanning."""

    file: Path
    agent_id: str
    command: str
    pool: str
    function_name: str


def _extract_string(node: ast.expr) -> str | None:
    """Extract a string value from an AST node, if it is a constant."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _get_keyword(keywords: list[ast.keyword], name: str) -> str | None:
    """Find a keyword argument by name and extract its string value."""
    for kw in keywords:
        if kw.arg == name:
            return _extract_string(kw.value)
    return None


def _scan_file(path: Path) -> list[DiscoveredAgent]:
    """Parse a single Python file and return discovered agents.

    Returns an empty list if the file cannot be parsed.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    # First pass: find partial assignments like ``writer = agent("writer")``.
    # Maps variable name -> agent_id.
    partials: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not isinstance(func, ast.Name) or func.id != "agent":
            continue
        # Extract agent_id from first positional arg or agent_id keyword.
        agent_id: str | None = None
        if call.args:
            agent_id = _extract_string(call.args[0])
        if agent_id is None:
            agent_id = _get_keyword(call.keywords, "agent_id")
        if agent_id is not None:
            partials[target.id] = agent_id

    # Second pass: find decorated function definitions.
    results: list[DiscoveredAgent] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            agent_entry = _match_decorator(decorator, partials)
            if agent_entry is not None:
                agent_id, command, pool = agent_entry
                results.append(
                    DiscoveredAgent(
                        file=path,
                        agent_id=agent_id,
                        command=command,
                        pool=pool,
                        function_name=node.name,
                    )
                )

    return results


def _match_decorator(
    decorator: ast.expr,
    partials: dict[str, str],
) -> tuple[str, str, str] | None:
    """Match a decorator node against known @agent patterns.

    Returns ``(agent_id, command, pool)`` or ``None``.
    """
    if not isinstance(decorator, ast.Call):
        return None

    func = decorator.func

    if isinstance(func, ast.Name):
        if func.id == "agent":
            # Direct form: @agent("writer", command="draft", pool="default")
            agent_id: str | None = None
            if decorator.args:
                agent_id = _extract_string(decorator.args[0])
            if agent_id is None:
                agent_id = _get_keyword(decorator.keywords, "agent_id")
            if agent_id is None:
                return None
            command = _get_keyword(decorator.keywords, "command") or _DEFAULT_COMMAND
            pool = _get_keyword(decorator.keywords, "pool") or _DEFAULT_POOL
            return agent_id, command, pool

        if func.id in partials:
            # Partial form: @writer(command="deep")
            agent_id = partials[func.id]
            command = _get_keyword(decorator.keywords, "command") or _DEFAULT_COMMAND
            pool = _get_keyword(decorator.keywords, "pool") or _DEFAULT_POOL
            return agent_id, command, pool

    return None


def _should_skip(name: str) -> bool:
    """Return True if a directory or file name should be skipped."""
    return name.startswith(".") or name in _SKIP_DIRS or name.startswith("test_")


def discover_agents(path: Path) -> list[DiscoveredAgent]:
    """Scan directory for @agent-decorated functions.

    Recursively scans all ``.py`` files under *path* for functions
    decorated with ``@agent(...)``. Skips files that fail to parse.

    Args:
        path: Directory to scan. If a file, scans just that file.

    Returns:
        List of discovered agents sorted by ``(agent_id, command)``.
    """
    if path.is_file():
        results = _scan_file(path)
    else:
        results = []
        for child in sorted(path.iterdir()):
            if child.is_dir():
                if _should_skip(child.name):
                    continue
                results.extend(discover_agents(child))
            elif child.is_file() and child.suffix == ".py":
                if _should_skip(child.name):
                    continue
                results.extend(_scan_file(child))

    results.sort(key=lambda a: (a.agent_id, a.command))
    return results
