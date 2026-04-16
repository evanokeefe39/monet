"""Tests for slash-command derivation on AgentManifest + MonetClient."""

from __future__ import annotations

from monet.core.manifest import RESERVED_SLASH, AgentManifest


def test_reserved_slash_contains_plan() -> None:
    assert "/plan" in RESERVED_SLASH


def test_slash_commands_empty_manifest_is_reserved_only() -> None:
    m = AgentManifest()
    assert m.slash_commands() == list(RESERVED_SLASH)


def test_slash_commands_derives_agent_command_format() -> None:
    m = AgentManifest()
    m.declare("researcher", "deep", description="")
    m.declare("writer", "draft")
    out = m.slash_commands()
    assert out[: len(RESERVED_SLASH)] == list(RESERVED_SLASH)
    assert "/researcher:deep" in out
    assert "/writer:draft" in out


def test_slash_commands_dedupes_duplicates() -> None:
    m = AgentManifest()
    m.declare("writer", "draft", pool="local")
    m.declare("writer", "draft", pool="local")  # idempotent re-declare
    out = m.slash_commands()
    assert out.count("/writer:draft") == 1


def test_slash_commands_does_not_duplicate_reserved() -> None:
    m = AgentManifest()
    # Nothing on the manifest can shadow /plan because the format is /agent:cmd.
    m.declare("plan", "fast")  # even a quirky agent named 'plan' stays distinct
    out = m.slash_commands()
    assert out.count("/plan") == 1
    assert "/plan:fast" in out


def test_slash_commands_reserved_first() -> None:
    m = AgentManifest()
    m.declare("writer", "draft")
    out = m.slash_commands()
    assert out[0] == "/plan"
