"""Build LangGraph RetryPolicy from agent capability descriptors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.types import RetryPolicy

if TYPE_CHECKING:
    from monet.descriptors import CommandDescriptor


def build_retry_policy(
    descriptor: CommandDescriptor,
) -> RetryPolicy:
    """Build a LangGraph RetryPolicy from a CommandDescriptor's retry config.

    Preconditions:
        descriptor.retry is populated with valid retry semantics.
    Postconditions:
        Returns a RetryPolicy configured for the command.
    """
    retry = descriptor.retry
    return RetryPolicy(
        max_attempts=retry.max_retries + 1,  # RetryPolicy counts attempts, not retries
        backoff_factor=retry.backoff_factor,
    )
