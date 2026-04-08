"""Smoke test for examples/social_media_llm/cli.py.

Verifies the example CLI imports cleanly against the SDK reference
stack. Does not run an end-to-end subprocess — that requires API keys
and is documented as a manual smoke run in the example README.
"""

from __future__ import annotations


def test_example_cli_imports() -> None:
    import examples.social_media_llm.cli as cli

    assert callable(cli.cli_main)
