"""Boundary errors raised by :class:`~monet.client.MonetClient` methods.

These are caller errors (bad arguments, wrong state, invalid sequencing) —
not graph errors. Graph-level failures surface as :class:`RunFailed`
events in the stream.
"""

from __future__ import annotations


class MonetClientError(Exception):
    """Base class for all client boundary errors."""


class RunNotInterrupted(MonetClientError):  # noqa: N818
    """No interrupted thread found for the given ``run_id``."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"no interrupted thread found for run {run_id!r}")
        self.run_id = run_id


class AlreadyResolved(MonetClientError):  # noqa: N818
    """Run has already moved past its interrupt — safe-retry guard."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id!r} is not currently paused at an interrupt")
        self.run_id = run_id


class AmbiguousInterrupt(MonetClientError):  # noqa: N818
    """Multiple next-nodes pending — caller must disambiguate."""

    def __init__(self, run_id: str, next_nodes: list[str]) -> None:
        super().__init__(
            f"run {run_id!r} has multiple pending interrupts: {next_nodes!r}"
        )
        self.run_id = run_id
        self.next_nodes = next_nodes


class InterruptTagMismatch(MonetClientError):  # noqa: N818
    """Resume ``tag`` does not match the graph's current interrupt node."""

    def __init__(self, run_id: str, expected: str, got: str) -> None:
        super().__init__(
            f"run {run_id!r}: resume tag {got!r} does not match "
            f"the graph's current interrupt {expected!r}"
        )
        self.run_id = run_id
        self.expected = expected
        self.got = got


class GraphNotInvocable(MonetClientError):  # noqa: N818
    """The graph id is not declared in ``monet.toml [entrypoints]``."""

    def __init__(self, graph_id: str, declared: list[str]) -> None:
        super().__init__(
            f"graph {graph_id!r} is not a declared entrypoint. "
            f"Add it to [entrypoints] in monet.toml. "
            f"Declared: {', '.join(sorted(declared)) or '(none)'}"
        )
        self.graph_id = graph_id
        self.declared = declared


__all__ = [
    "AlreadyResolved",
    "AmbiguousInterrupt",
    "GraphNotInvocable",
    "InterruptTagMismatch",
    "MonetClientError",
    "RunNotInterrupted",
]
