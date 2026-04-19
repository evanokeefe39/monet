"""Tests for slash-command derivation on CapabilityIndex + MonetClient."""

from __future__ import annotations

from monet.server._capabilities import RESERVED_SLASH, Capability, CapabilityIndex


def _cap(agent_id: str, command: str, pool: str = "local") -> Capability:
    return Capability(agent_id=agent_id, command=command, pool=pool)


def test_reserved_slash_contains_plan() -> None:
    assert "/plan" in RESERVED_SLASH


def test_slash_commands_empty_index_is_reserved_only() -> None:
    idx = CapabilityIndex()
    assert idx.slash_commands() == list(RESERVED_SLASH)


def test_slash_commands_derives_agent_command_format() -> None:
    idx = CapabilityIndex()
    idx.upsert_worker(
        "w", "local", [_cap("researcher", "deep"), _cap("writer", "draft")]
    )
    out = idx.slash_commands()
    assert out[: len(RESERVED_SLASH)] == list(RESERVED_SLASH)
    assert "/researcher:deep" in out
    assert "/writer:draft" in out


def test_slash_commands_dedupes_duplicates() -> None:
    idx = CapabilityIndex()
    idx.upsert_worker("w1", "local", [_cap("writer", "draft")])
    idx.upsert_worker("w2", "local", [_cap("writer", "draft")])
    out = idx.slash_commands()
    assert out.count("/writer:draft") == 1


def test_slash_commands_does_not_duplicate_reserved() -> None:
    idx = CapabilityIndex()
    # An agent named 'plan' stays distinct because slash format is /agent:cmd.
    idx.upsert_worker("w", "local", [_cap("plan", "fast")])
    out = idx.slash_commands()
    assert out.count("/plan") == 1
    assert "/plan:fast" in out


def test_slash_commands_reserved_first() -> None:
    idx = CapabilityIndex()
    idx.upsert_worker("w", "local", [_cap("writer", "draft")])
    out = idx.slash_commands()
    assert out[0] == "/plan"
